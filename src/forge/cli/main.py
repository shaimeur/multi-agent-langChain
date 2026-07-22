"""The `forge` CLI — cahier 10.2.

Not an accessory: a terminal client is the honest usage mode for an engineering
assistant, and a Rich live panel is a better agent-activity demo than either web
option. Commands are stubbed here and filled in as their subsystems land.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
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
def ask(question: str = typer.Argument(..., help="Question about the codebase.")) -> None:
    """Grounded question against the indexed codebase (cahier 6.6). Lands D3."""
    raise typer.Exit(_todo("ask", "D3 — hybrid retrieval and grounded generation"))


@app.command()
def fix(request: str = typer.Argument(..., help="Bug report or change request.")) -> None:
    """Full plan → patch → test → review cycle (cahier 5.1). Lands D8."""
    raise typer.Exit(_todo("fix", "D8 — the implement loop"))


def _todo(command: str, when: str) -> int:
    console.print(f"[yellow]`forge {command}` is not implemented yet.[/] Scheduled: {when}.")
    return 1


if __name__ == "__main__":
    app()
