"""The D8 repair benchmark — seed a bug, let the loop fix it, grade it blind.

    uv run python evals/run_swe_mini.py --verify        # no model: are the bugs sound?
    uv run python evals/run_swe_mini.py                 # the real run (needs a key)
    uv run python evals/run_swe_mini.py --limit 1       # one bug, to spend one quota

``--verify`` is the mode that matters before you trust a score: it seeds each bug and
checks the hidden test is green on clean code, red once seeded, and green again after
the reference fix. It needs no LLM, so it runs in CI and it is what catches a target
repo bump silently invalidating the benchmark.

``--limit`` exists because the ceiling here is free-tier quota, not code: descope §7
cut the cahier's ten bugs to four, and running N of them is a flag rather than an edit.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

# Run as a script (`python evals/run_swe_mini.py`), sys.path[0] is evals/, so the
# `evals` package itself is not importable. Put the project root on the path before
# importing it, so the invocation matches the other runners in this directory.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from evals.swe_mini.bugs import BUGS  # noqa: E402
from evals.swe_mini.harness import BugResult, grade, seed, verify  # noqa: E402
from forge.config import LLMRole, get_settings  # noqa: E402
from forge.core.agents.reviewer import shares_family_with_editor  # noqa: E402
from forge.core.workspace import session_workspace  # noqa: E402

console = Console()


def _verify(limit: int) -> int:
    """Prove each bug is seedable, detectable and fixable. No model involved."""
    settings = get_settings()
    table = Table(title="swe_mini — harness self-check", header_style="bold")
    table.add_column("bug")
    table.add_column("title")
    table.add_column("verdict")
    failures = 0

    for bug in BUGS[:limit]:
        with session_workspace(f"verify-{bug.bug_id}", settings=settings) as workspace:
            problem = verify(workspace, bug, settings=settings)
        ok = not problem
        failures += 0 if ok else 1
        table.add_row(
            bug.bug_id,
            bug.title,
            "[green]sound[/green]" if ok else f"[red]{problem}[/red]",
        )

    console.print(table)
    if failures:
        console.print(f"[red]{failures} bug(s) unsound — the benchmark cannot be trusted.[/red]")
    return 1 if failures else 0


def _repair(limit: int) -> int:
    """Seed each bug, run the implement loop against it, grade with the hidden test."""
    from forge.core.loop import build_implement_loop
    from forge.core.state import Budget
    from forge.llm.provider import build_llm
    from forge.models import (
        ChangePlan,
        Chunk,
        ChunkKind,
        CitationRef,
        ContextPack,
        PlanStep,
    )

    settings = get_settings()
    coder = build_llm(LLMRole.CODER, settings=settings)
    # §4/A5 wants the critic on a different family from the editor; under one provider
    # it resolves to the same model, which is worth saying out loud rather than implying.
    reviewer = build_llm(LLMRole.REASONER, settings=settings)
    if shares_family_with_editor(settings):
        console.print(
            "[yellow]note: reviewer and editor resolve to the same model — "
            "the critic shares the editor's blind spots (cahier §4/A5).[/yellow]"
        )
    results: list[BugResult] = []

    for bug in BUGS[:limit]:
        with session_workspace(f"swe-{bug.bug_id}", settings=settings) as workspace:
            seed(workspace, bug)
            # The agent is given the bug report and the seeded file — never the hidden
            # test, and never the reference fix. The pack stands in for retrieval: the
            # reviewer's grounding point resolves the plan's citation against it.
            source = workspace.read(bug.path)
            pack = ContextPack(
                chunks=[
                    Chunk(
                        chunk_id="seeded",
                        repo="target",
                        path=bug.path,
                        language="python",
                        kind=ChunkKind.MODULE,
                        start_line=1,
                        end_line=source.count("\n") + 1,
                        text=source,
                        raw=source,
                    )
                ]
            )
            plan = ChangePlan(
                summary=bug.title,
                steps=[
                    PlanStep(
                        intent=bug.report,
                        target_path=bug.path,
                        evidence=[CitationRef(chunk_id="seeded", why="the reported file")],
                    )
                ],
            )
            loop = build_implement_loop(
                coder_llm=coder, reviewer_llm=reviewer, workspace=workspace, settings=settings
            )
            final = loop.invoke({"plan": plan, "pack": pack, "budget": Budget(), "iterations": 0})
            results.append(
                grade(workspace, bug, settings=settings, iterations=final.get("iterations", 0))
            )

    table = Table(title="swe_mini — repair results", header_style="bold")
    for column in ("bug", "status", "iterations", "detail"):
        table.add_column(column)
    for result in results:
        colour = {"REPAIRED": "green", "REGRESSED": "yellow"}.get(result.status, "red")
        table.add_row(
            result.bug_id,
            f"[{colour}]{result.status}[/{colour}]",
            str(result.iterations),
            result.detail,
        )
    console.print(table)

    repaired = sum(1 for r in results if r.status == "REPAIRED")
    console.print(f"\n[bold]{repaired}/{len(results)} repaired[/bold]")
    return 0 if repaired == len(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Self-check the bugs instead of repairing them. Needs no model.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=len(BUGS),
        help=f"How many bugs to run (default all {len(BUGS)}).",
    )
    args = parser.parse_args()
    limit = max(1, min(args.limit, len(BUGS)))

    return _verify(limit) if args.verify else _repair(limit)


if __name__ == "__main__":
    sys.exit(main())
