"""The `forge` CLI — cahier 10.2.

Not an accessory: a terminal client is the honest usage mode for an engineering
assistant, and a Rich live panel is a better agent-activity demo than either web
option. Commands are stubbed here and filled in as their subsystems land.
"""

from __future__ import annotations

import asyncio
import subprocess
import uuid
from pathlib import Path

import typer
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

from forge.config import LLMRole, get_settings
from forge.models import GroundedAnswer

app = typer.Typer(
    name="forge",
    help="Multi-agent engineering assistant.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


@app.command()
def config() -> None:
    """Show the resolved configuration and where it came from."""
    settings = get_settings()

    table = Table(title="FORGE configuration", header_style="bold")
    table.add_column("Setting")
    table.add_column("Value")

    table.add_row("cache mode", settings.cache_mode.value)
    table.add_row("offline", "yes" if settings.offline else "no")
    table.add_row("provider", settings.llm_provider.value)
    for role in LLMRole:
        # No square brackets here — Rich would read them as markup and eat them.
        table.add_row(f"  model · {role.value}", settings.model_name(role))
    table.add_row("embedding model", settings.embedding_model)
    table.add_row("reranker", "on" if settings.rerank_enabled else "off (eval harness only)")
    table.add_row("qdrant", settings.qdrant_url or f"embedded @ {settings.qdrant_path}")
    table.add_row("target repo", str(settings.target_repo))

    console.print(table)

    if not settings.secret_values():
        console.print(
            "\n[yellow]No API keys configured.[/] That is fine in "
            "[bold]CACHE_MODE=replay[/] — the committed fixtures cover the demo."
        )


@app.command()
def index(
    path: Path = typer.Argument(..., help="Repository to ingest."),
    full: bool = typer.Option(False, "--full", help="Rebuild instead of reindexing the git diff."),
) -> None:
    """Ingest and index a repository (cahier 6.1)."""
    from forge.rag.ingest import index_repo

    if not path.is_dir():
        console.print(f"[red]Not a directory:[/] {path}")
        raise typer.Exit(1)

    with console.status(f"Indexing {path}..."):
        report = index_repo(path, full=full)

    console.print(f"[green]{report.summary()}[/]")
    if report.deleted:
        console.print(f"  replaced chunks from {report.deleted} changed file(s)")


@app.command()
def search(
    query: str = typer.Argument(..., help="Natural-language question or a bare symbol."),
    k: int = typer.Option(8, "--k", help="How many hits to return."),
    language: str | None = typer.Option(
        None, "--language", help="Filter: python, markdown, config."
    ),
    path: str | None = typer.Option(None, "--path", help="Filter: repo-relative path prefix."),
) -> None:
    """Hybrid retrieval over the indexed repo (cahier 6.5).

    Identifier-shaped queries (``parse_config``, ``SessionManager``) take the
    lexical path — ripgrep + sparse; everything else takes dense + sparse. RRF
    fuses the rankings. The table shows which retrievers surfaced each hit.
    """
    from forge.rag.retrieve import Filters, hybrid_search, is_identifier_query

    route = (
        "lexical (ripgrep + sparse)" if is_identifier_query(query) else "semantic (dense + sparse)"
    )
    with console.status(f"Searching [{route}]..."):
        hits = hybrid_search(query, k=k, filters=Filters(language=language, path_prefix=path))

    if not hits:
        console.print(
            "[yellow]No hits.[/] Is the repo indexed? Run [bold]forge index data/target[/] first."
        )
        raise typer.Exit(1)

    table = Table(title=f"{query!r}  ·  {route}", header_style="bold")
    table.add_column("#", justify="right", style="dim")
    table.add_column("location")
    table.add_column("symbol")
    table.add_column("kind")
    table.add_column("retrievers")
    table.add_column("score", justify="right")
    for rank, hit in enumerate(hits, start=1):
        c = hit.chunk
        table.add_row(
            str(rank),
            f"{c.path}:{c.start_line}-{c.end_line}",
            c.symbol or "—",
            c.kind.value,
            hit.provenance,
            f"{hit.score:.4f}",
        )
    console.print(table)


@app.command()
def ask(
    question: str = typer.Argument(..., help="Question about the codebase."),
    k: int = typer.Option(8, "--k", help="How many snippets to ground the answer in."),
    session: str | None = typer.Option(
        None,
        "--session",
        "-s",
        help="Run on the multi-agent graph under this session id: checkpointed, "
        "multi-turn, and it resumes after a restart. Without it, the direct RAG path.",
    ),
) -> None:
    """Grounded answer with verified citations (cahier 6.6).

    Without ``--session``: the direct RAG path — retrieve, answer from the snippets,
    verify every citation in code. With ``--session``: the LangGraph (D5) —
    supervisor → retriever → answerer with sliding-summary memory, checkpointed to
    SQLite so the same session id resumes across processes. Needs an LLM: local
    Ollama by default (see .env.local.example), or a provider key.
    """
    if session is not None:
        result = asyncio.run(_ask_graph(question, session))
    else:
        from forge.rag.answer import answer_question

        with console.status("Retrieving and answering..."):
            result = answer_question(question, k=k)

    _render_answer(result)


def _render_answer(result: GroundedAnswer) -> None:
    badge = "[green]● grounded[/]" if result.grounded else "[yellow]○ ungrounded[/]"
    console.print()
    console.print(Panel(result.answer, title=f"forge ask · {badge}", border_style="cyan"))

    if result.citations:
        symbols = {s.chunk_id: s.symbol for s in result.sources}
        table = Table(
            title="citations · verified against the retrieved context", header_style="bold"
        )
        table.add_column("location")
        table.add_column("symbol")
        for citation in result.citations:
            table.add_row(
                f"{citation.path}:{citation.start_line}-{citation.end_line}",
                symbols.get(citation.chunk_id) or "—",
            )
        console.print(table)
    elif result.sources:
        console.print(
            "[yellow]No verified citation.[/] The model did not ground its answer in the "
            f"{len(result.sources)} retrieved snippet(s) — treat the answer with suspicion."
        )


def _step_line(node: str, delta: dict) -> str:
    """One line of the live agent timeline for a streamed node update."""
    if node == "supervisor":
        route = (delta or {}).get("route")
        where = getattr(route, "route", "…")
        return f"[cyan]●[/] supervisor → [bold]{getattr(where, 'value', where)}[/]"
    if node == "retriever":
        pack = (delta or {}).get("pack")
        n = len(getattr(pack, "chunks", []) or [])
        return f"[cyan]●[/] retriever · {n} chunks packed"
    if node == "answer":
        ans = (delta or {}).get("answer")
        state = "grounded" if getattr(ans, "grounded", False) else "ungrounded"
        return f"[cyan]●[/] answerer · {state}"
    if node == "summary":
        return "[cyan]●[/] summary · folded older turns"
    return f"[cyan]●[/] {node}"


async def _ask_graph(question: str, session_id: str) -> GroundedAnswer:
    """Stream one turn through the graph, showing a live agent timeline."""
    from langchain_core.messages import HumanMessage

    from forge.core.checkpoint import sqlite_checkpointer
    from forge.core.graph import build_default_nodes, build_graph

    settings = get_settings()
    with console.status("Loading models and index..."):
        nodes = build_default_nodes(settings)

    config = {"configurable": {"thread_id": session_id}}
    steps: list[str] = []

    def panel() -> Panel:
        grid = Table.grid(padding=(0, 1))
        for line in steps or ["[dim]starting…[/]"]:
            grid.add_row(line)
        return Panel(grid, title=f"forge · session {session_id}", border_style="cyan")

    async with sqlite_checkpointer(settings.checkpoint_db) as checkpointer:
        graph = build_graph(nodes, checkpointer=checkpointer)
        with Live(panel(), console=console, refresh_per_second=8) as live:
            async for update in graph.astream(
                {"messages": [HumanMessage(content=question)], "session_id": session_id},
                config=config,
                stream_mode="updates",
            ):
                for node, delta in update.items():
                    steps.append(_step_line(node, delta))
                    live.update(panel())
        snapshot = await graph.aget_state(config)

    answer = snapshot.values.get("answer")
    if answer is None:
        return GroundedAnswer(question=question, answer="No answer was produced.", grounded=False)
    return answer if isinstance(answer, GroundedAnswer) else GroundedAnswer(**answer)


def _fix_step_line(node: str, delta: dict) -> str:
    """One line of the change-path timeline. Mirrors `_step_line` for the Q&A graph."""
    delta = delta or {}
    if node == "retriever":
        pack = delta.get("pack")
        n = len(getattr(pack, "chunks", []) or [])
        return f"[cyan]●[/] retriever · {n} chunks packed"
    if node == "planner":
        plan = delta.get("plan")
        steps = getattr(plan, "steps", []) or []
        return f"[cyan]●[/] planner · {len(steps)} grounded step(s)"
    if node == "regression":
        red = delta.get("regression_red")
        state = "[red]red[/] (reproduces the bug)" if red else "[yellow]green — pins nothing[/]"
        return f"[cyan]●[/] tester · regression test {state}"
    if node == "editor":
        ok = delta.get("patch_ok")
        return f"[cyan]●[/] editor · patch {'applies' if ok else '[red]does not apply[/]'}"
    if node == "apply":
        return "[cyan]●[/] apply · written to the worktree"
    if node == "verify":
        report = delta.get("report")
        return f"[cyan]●[/] sandbox · {getattr(report, 'headline', lambda: '…')()}"
    if node == "reviewer":
        review = delta.get("review")
        verdict = getattr(getattr(review, "verdict", None), "value", "…")
        colour = "green" if verdict == "approve" else "yellow"
        return f"[cyan]●[/] reviewer · [{colour}]{verdict}[/]"
    if node == "escalate":
        return "[yellow]●[/] escalated to you — the editor and reviewer keep disagreeing"
    return f"[cyan]●[/] {node}"


def _approval_prompt(payload: dict) -> bool:
    """Render a §5.5 gate and ask. This is the human in human-in-the-loop."""
    kind = payload.get("kind", "approval")
    if kind == "plan_approval":
        table = Table(box=None, padding=(0, 1))
        table.add_column("#", style="dim")
        table.add_column("file", style="cyan")
        table.add_column("intent")
        for index, step in enumerate(payload.get("steps", []), start=1):
            table.add_row(str(index), step.get("target_path", ""), step.get("intent", ""))
        console.print(Panel(table, title="Plan — approve?", border_style="yellow"))
    elif kind == "patch_approval":
        console.print(
            Panel(
                Syntax(payload.get("diff", ""), "diff", theme="ansi_dark", word_wrap=True),
                title=f"Patch — {', '.join(payload.get('files', []))}",
                border_style="yellow",
            )
        )
    else:
        console.print(Panel(str(payload), title=kind, border_style="yellow"))
    return typer.confirm("Approve?", default=True)


async def _run_fix(request: str, session_id: str) -> int:
    """Drive the change graph, pausing at each gate to ask. Returns an exit code."""
    from langchain_core.messages import HumanMessage
    from langgraph.types import Command

    from forge.config import LLMRole
    from forge.core.checkpoint import sqlite_checkpointer
    from forge.core.graph import build_default_retriever
    from forge.core.loop import build_change_graph
    from forge.core.workspace import session_workspace
    from forge.llm.provider import build_llm

    settings = get_settings()
    steps: list[str] = []

    def panel() -> Panel:
        grid = Table.grid(padding=(0, 1))
        for line in steps or ["[dim]starting…[/]"]:
            grid.add_row(line)
        return Panel(grid, title=f"forge fix · session {session_id}", border_style="cyan")

    try:
        with console.status("Loading models..."):
            planner = build_llm(LLMRole.REASONER, settings=settings)
            coder = build_llm(LLMRole.CODER, settings=settings)
    except Exception as error:
        # Cahier §9: a missing key is a configuration problem, and the user should be
        # told which knob to turn — not shown a pydantic validation dump.
        console.print(
            f"[red]No usable model for provider '{settings.llm_provider.value}'.[/]\n"
            f"  {str(error).splitlines()[0][:160]}\n"
            "  Set the provider's API key in .env, or use LLM_PROVIDER=ollama "
            "for the offline profile."
        )
        return 1

    try:
        with console.status("Loading the index..."):
            retriever = build_default_retriever(settings)
    except Exception as error:
        # A missing index is a different failure from a missing key and deserves its
        # own sentence: the planner can only cite what was ingested, so pointing the
        # user at their API key here would send them to the wrong knob.
        console.print(
            f"[red]No usable index for '{settings.target_repo}'.[/]\n"
            f"  {str(error).splitlines()[0][:160]}\n"
            "  Run `forge index` first, or point QDRANT_URL/QDRANT_PATH at the index."
        )
        return 1

    config = {"configurable": {"thread_id": session_id}}
    with session_workspace(session_id, settings=settings) as workspace:
        async with sqlite_checkpointer(settings.checkpoint_db) as checkpointer:
            graph = build_change_graph(
                planner_llm=planner,
                coder_llm=coder,
                reviewer_llm=planner,
                workspace=workspace,
                settings=settings,
                checkpointer=checkpointer,
                retriever_node=retriever,
            )
            payload: object = {
                "messages": [HumanMessage(content=request)],
                "session_id": session_id,
            }

            # Each pass runs until the graph ends or stops at a gate; an approval
            # resumes it. The loop is bounded by the gates themselves — two of them.
            for _ in range(6):
                with Live(panel(), console=console, refresh_per_second=8) as live:
                    async for update in graph.astream(
                        payload, config=config, stream_mode="updates"
                    ):
                        for node, delta in update.items():
                            if node == "__interrupt__":
                                continue
                            steps.append(_fix_step_line(node, delta))
                            live.update(panel())

                snapshot = await graph.aget_state(config)
                pending = [
                    t for t in getattr(snapshot, "tasks", ()) if getattr(t, "interrupts", None)
                ]
                if not pending:
                    break
                approved = _approval_prompt(pending[0].interrupts[0].value)
                payload = Command(resume={"approved": approved})

            final = (await graph.aget_state(config)).values

        return _render_fix_result(final, workspace)


def _render_fix_result(final: dict, workspace) -> int:
    """Show the outcome. Returns the process exit code."""
    halted = final.get("halted")
    if halted:
        console.print(f"\n[yellow]Stopped:[/] {halted}")
        return 1

    review = final.get("review")
    report = final.get("report")
    if review is not None:
        console.print("\n[bold]Reviewer checklist[/]")
        for check in getattr(review, "checks", []):
            mark = "[green]✓[/]" if check.passed else "[red]✗[/]"
            who = "code" if check.programmatic else "model"
            console.print(f"  {mark} [dim]{who:>5}[/] {check.check.value} — {check.justification}")
    if report is not None:
        console.print(f"\n[bold]Tests:[/] {report.headline()}")

    diff = subprocess.run(
        ["git", "-C", str(workspace.path), "diff"], capture_output=True, text=True
    ).stdout
    if diff.strip():
        console.print(Panel(Syntax(diff, "diff", theme="ansi_dark"), title="Verified diff"))
    else:
        console.print("[yellow]No change was produced.[/]")

    approved = getattr(review, "approved", False)
    return 0 if approved else 1


@app.command()
def fix(
    request: str = typer.Argument(..., help="Bug report or change request."),
    session: str = typer.Option("", "--session", "-s", help="Session id (resumes if it exists)."),
) -> None:
    """Full plan → patch → test → review cycle, with both approval gates (cahier §5.1)."""
    session_id = session or f"fix-{uuid.uuid4().hex[:8]}"
    raise typer.Exit(asyncio.run(_run_fix(request, session_id)))


if __name__ == "__main__":
    app()
