"""The `forge` CLI — cahier 10.2.

Not an accessory: a terminal client is the honest usage mode for an engineering
assistant, and a Rich live panel is a better agent-activity demo than either web
option. Commands are stubbed here and filled in as their subsystems land.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from forge.config import LLMRole, get_settings

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
) -> None:
    """Grounded answer with verified citations (cahier 6.6).

    Direct RAG — retrieve, answer from the snippets, then verify every citation in
    code against what was retrieved. Not the full agent graph (that is D5-D9); the
    smallest thing that is a usable service. Needs an LLM: local Ollama by default
    (see .env.local.example), or a provider key.
    """
    from forge.rag.answer import answer_question

    with console.status("Retrieving and answering..."):
        result = answer_question(question, k=k)

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


@app.command()
def fix(request: str = typer.Argument(..., help="Bug report or change request.")) -> None:
    """Full plan → patch → test → review cycle (cahier 5.1). Lands D8."""
    raise typer.Exit(_todo("fix", "D8 — the implement loop"))


def _todo(command: str, when: str) -> int:
    console.print(f"[yellow]`forge {command}` is not implemented yet.[/] Scheduled: {when}.")
    return 1


if __name__ == "__main__":
    app()
