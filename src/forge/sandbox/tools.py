"""``run_pytest`` / ``run_python`` / ``run_linter`` — cahier §4 (SANDBOX_ENGINEER).

Every one returns an ``ExecutionReport``, never prose. That is the contract that
makes the repair loop deterministic: D8 routes on ``outcome`` and quotes
``failures`` as evidence, rather than asking a model to interpret a transcript it
half-read.

The agent never chooses *where* a command runs. The working directory is bound to
the session worktree when the tools are built, and the only thing a model supplies
is a list of targets — each resolved through ``Workspace.resolve`` and refused if it
escapes, and refused outright if it looks like a flag. A model that could pass
``--rootdir=/`` or ``-p some_plugin`` would be choosing what the sandbox executes,
which is the one decision it must not have.
"""

from __future__ import annotations

from collections.abc import Sequence

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from forge.config import Settings, get_settings
from forge.core.workspace import Workspace
from forge.models import ExecutionOutcome, ExecutionReport, Isolation
from forge.sandbox.report import parse_pytest_output, parse_ruff_violations, pytest_outcome
from forge.sandbox.runner import RawResult, SandboxUnavailable, active_isolation, run_in_sandbox

# -q keeps a transcript inside the 64 KB cap; -rfE forces the short summary the
# failing-test parser reads; no:cacheprovider keeps pytest from dropping a
# .pytest_cache into the worktree, which is the tree the session's diff comes from.
_PYTEST_ARGV = [
    "python",
    "-m",
    "pytest",
    "-q",
    "-rfE",
    "--color=no",
    "-p",
    "no:cacheprovider",
]

_RUFF_ARGV = ["ruff", "check", "--output-format=concise", "--no-cache"]


def _safe_targets(workspace: Workspace, targets: Sequence[str] | None) -> list[str]:
    """Validate model-supplied targets: inside the worktree, and not a flag."""
    checked: list[str] = []
    for target in targets or []:
        if target.startswith("-"):
            raise ValueError(f"refusing a flag as a target: {target!r}")
        # `tests/test_calc.py::test_add` — only the path half is a path.
        workspace.resolve(target.split("::", 1)[0])
        checked.append(target)
    return checked


def _report(
    command: list[str],
    raw: RawResult,
    outcome: ExecutionOutcome,
    **fields,
) -> ExecutionReport:
    return ExecutionReport(
        command=command,
        outcome=outcome,
        isolation=raw.isolation,
        exit_code=raw.exit_code,
        duration_s=raw.duration_s,
        stdout=raw.stdout,
        stderr=raw.stderr,
        truncated=raw.truncated,
        **fields,
    )


def _generic_outcome(exit_code: int | None) -> ExecutionOutcome:
    if exit_code is None:
        return ExecutionOutcome.TIMEOUT
    return ExecutionOutcome.PASSED if exit_code == 0 else ExecutionOutcome.FAILED


# --- the three tools ------------------------------------------------------


def run_pytest(
    workspace: Workspace,
    targets: Sequence[str] | None = None,
    *,
    coverage: bool = False,
    settings: Settings | None = None,
    timeout_s: int | None = None,
) -> ExecutionReport:
    """Run the suite (or specific node ids) in the sandbox and parse the result."""
    settings = settings or get_settings()
    argv = [*_PYTEST_ARGV]
    if coverage:
        # A single percentage; the *delta* D8 wants is two of these subtracted.
        argv += ["--cov", "--cov-report=term"]
    argv += _safe_targets(workspace, targets)

    raw = run_in_sandbox(argv, workspace.path, settings=settings, timeout_s=timeout_s)
    counts = parse_pytest_output(raw.stdout + "\n" + raw.stderr)
    return _report(
        argv,
        raw,
        pytest_outcome(raw.exit_code),
        passed=counts.passed,
        failed=counts.failed,
        errors=counts.errors,
        skipped=counts.skipped,
        failures=counts.failures,
        coverage_percent=counts.coverage_percent,
    )


def run_python(
    workspace: Workspace,
    code: str,
    *,
    settings: Settings | None = None,
    timeout_s: int | None = None,
) -> ExecutionReport:
    """Execute a snippet in the sandbox — a scratch check, not a verdict on the repo.

    ``code`` is passed as an argument rather than written into the worktree: a probe
    that leaves a file behind would show up in the session's diff.
    """
    argv = ["python", "-c", code]
    raw = run_in_sandbox(argv, workspace.path, settings=settings, timeout_s=timeout_s)
    # The command is recorded without the payload — a 4 KB snippet in every trace
    # line is noise, and the snippet is already in the caller's own state.
    return _report(["python", "-c", "<snippet>"], raw, _generic_outcome(raw.exit_code))


def run_linter(
    workspace: Workspace,
    targets: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    timeout_s: int | None = None,
) -> ExecutionReport:
    """ruff over the worktree. Violations come back shaped like test failures."""
    argv = [*_RUFF_ARGV, *(_safe_targets(workspace, targets) or ["."])]
    raw = run_in_sandbox(argv, workspace.path, settings=settings, timeout_s=timeout_s)

    violations = parse_ruff_violations(raw.stdout)
    if raw.exit_code is None:
        outcome = ExecutionOutcome.TIMEOUT
    elif raw.exit_code == 0:
        outcome = ExecutionOutcome.PASSED
    elif raw.exit_code == 1:
        outcome = ExecutionOutcome.FAILED  # violations found — a result, not a crash
    else:
        outcome = ExecutionOutcome.ERROR  # ruff itself refused: bad config, bad path
    return _report(argv, raw, outcome, failed=len(violations), failures=violations)


# --- LangChain bindings ---------------------------------------------------


class PytestArgs(BaseModel):
    targets: list[str] = Field(
        default_factory=list,
        description="Repo-relative files or pytest node ids. Empty runs the whole suite.",
    )
    coverage: bool = Field(default=False, description="Also collect a coverage percentage.")


class PythonArgs(BaseModel):
    code: str = Field(description="Python source to execute in the sandbox.")


class LinterArgs(BaseModel):
    targets: list[str] = Field(
        default_factory=list,
        description="Repo-relative paths to lint. Empty lints the whole worktree.",
    )


def _guarded(function):
    """Turn the two failure modes an agent must not crash on into typed reports.

    A missing Docker socket or a rejected target is a real answer the graph can route
    on; letting either raise would take down the turn and lose the run's context.
    """

    def call(**kwargs) -> ExecutionReport:
        try:
            return function(**kwargs)
        except (SandboxUnavailable, ValueError) as error:
            return ExecutionReport(
                command=[],
                outcome=ExecutionOutcome.ERROR,
                isolation=active_isolation(),
                stderr=str(error),
            )

    return call


def make_sandbox_tools(workspace: Workspace, *, settings: Settings | None = None) -> list[BaseTool]:
    """The three tools, bound to one session worktree.

    Built per session rather than imported as module-level singletons precisely
    because of the binding: a tool that took its working directory from the model
    would let the model point the sandbox at any path on the host.
    """
    settings = settings or get_settings()

    return [
        StructuredTool.from_function(
            func=_guarded(
                lambda targets, coverage=False: run_pytest(
                    workspace, targets, coverage=coverage, settings=settings
                )
            ),
            name="run_pytest",
            description=(
                "Run the test suite inside the hardened sandbox and return a structured "
                "ExecutionReport: outcome, counts, failing test ids, stderr tail, duration."
            ),
            args_schema=PytestArgs,
        ),
        StructuredTool.from_function(
            func=_guarded(lambda code: run_python(workspace, code, settings=settings)),
            name="run_python",
            description=(
                "Execute a short Python snippet inside the hardened sandbox (no network, "
                "read-only root) and return a structured ExecutionReport."
            ),
            args_schema=PythonArgs,
        ),
        StructuredTool.from_function(
            func=_guarded(lambda targets: run_linter(workspace, targets, settings=settings)),
            name="run_linter",
            description=(
                "Run ruff over the worktree inside the sandbox. Violations are returned in "
                "the same shape as test failures, in ExecutionReport.failures."
            ),
            args_schema=LinterArgs,
        ),
    ]


__all__ = [
    "Isolation",
    "make_sandbox_tools",
    "run_linter",
    "run_pytest",
    "run_python",
]
