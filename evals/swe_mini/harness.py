"""Seed a bug into a worktree, then grade the repair against the hidden test.

The grading rule is deliberately narrow: **the hidden test decides**. Not the agent's
own regression test, which it wrote and could have written to pass; not the rest of
the suite, which a sufficiently destructive "fix" could also satisfy by deleting
things. Both of those are checked too — a repair that breaks unrelated tests is not a
repair — but the hidden test is what "fixed" means.

The hidden test is written into the worktree only at grading time and removed
afterwards, so it is never on disk while the agent is looking at the tree.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass

from evals.swe_mini.bugs import SeededBug
from forge.config import Settings, get_settings
from forge.core.workspace import Workspace
from forge.models import ExecutionReport
from forge.sandbox.tools import run_pytest
from forge.tools.patch import apply_patchset


@dataclass(frozen=True)
class BugResult:
    """How one seeded bug went."""

    bug_id: str
    repaired: bool
    """The hidden test passed — the only definition of success."""
    regressions: bool
    """Something else in the suite broke. A 'fix' that costs more than it buys."""
    iterations: int = 0
    detail: str = ""

    @property
    def status(self) -> str:
        if self.repaired and not self.regressions:
            return "REPAIRED"
        return "REGRESSED" if self.repaired else "FAILED"


def seed(workspace: Workspace, bug: SeededBug) -> None:
    """Break the worktree. Raises rather than returning False: a benchmark that
    silently failed to seed would score the agent on already-correct code."""
    result = apply_patchset(workspace, bug.break_patchset())
    if not result.ok:
        raise RuntimeError(f"could not seed {bug.bug_id}: {result.message}")


@contextmanager
def hidden_test(workspace: Workspace, bug: SeededBug):
    """Put the hidden test on disk for the duration of a grading run only."""
    path = workspace.resolve(bug.hidden_test_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(bug.hidden_test, encoding="utf-8")
    try:
        yield bug.hidden_test_path
    finally:
        path.unlink(missing_ok=True)


def run_hidden_test(
    workspace: Workspace, bug: SeededBug, *, settings: Settings | None = None
) -> ExecutionReport:
    settings = settings or get_settings()
    with hidden_test(workspace, bug) as target:
        return run_pytest(workspace, [target], settings=settings)


def grade(
    workspace: Workspace, bug: SeededBug, *, settings: Settings | None = None, iterations: int = 0
) -> BugResult:
    """Did the hidden test pass, and did anything else break doing it?"""
    settings = settings or get_settings()
    hidden = run_hidden_test(workspace, bug, settings=settings)
    suite = run_pytest(workspace, settings=settings)
    return BugResult(
        bug_id=bug.bug_id,
        repaired=hidden.ok,
        regressions=not suite.ok,
        iterations=iterations,
        detail=hidden.headline(),
    )


def verify(workspace: Workspace, bug: SeededBug, *, settings: Settings | None = None) -> str:
    """Self-check one bug without any model: is it *seedable, detectable and fixable*?

    Run before the benchmark means anything. A bug whose hidden test is already red on
    clean code, or still green after seeding, measures the harness rather than the
    agent — and a target-repo bump is exactly the thing that silently causes it.
    """
    settings = settings or get_settings()

    clean = run_hidden_test(workspace, bug, settings=settings)
    if not clean.ok:
        return f"hidden test is already failing on clean code: {clean.headline()}"

    seed(workspace, bug)
    broken = run_hidden_test(workspace, bug, settings=settings)
    if broken.ok:
        return "hidden test still passes after seeding — it does not detect the bug"

    fixed = apply_patchset(workspace, bug.repair_patchset())
    if not fixed.ok:
        return f"reference fix does not apply: {fixed.message}"
    repaired = run_hidden_test(workspace, bug, settings=settings)
    if not repaired.ok:
        return f"hidden test still fails after the reference fix: {repaired.headline()}"
    return ""
