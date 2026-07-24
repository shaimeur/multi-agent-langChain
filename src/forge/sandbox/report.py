"""Turn raw sandbox output into the structured fields of an ``ExecutionReport``.

Parsing lives here rather than in the runner because the runner's job is
confinement and the parser's job is meaning — and because these are the functions
worth testing against real pytest transcripts without spawning a container.

The rule throughout: **the exit code is the authority, the text is the detail.**
Output can be truncated at 64 KB (``sandbox_max_output_bytes``) and a truncated
transcript loses the summary line pytest prints last, so counts may be missing
while the verdict is still exactly right. Nothing here ever overrides the outcome
that ``runner.py`` derived from the exit status.
"""

from __future__ import annotations

import re

from forge.models import ExecutionOutcome, TestFailure

# pytest's own exit codes (documented, stable since 5.0). The gap between 1 and the
# rest is the whole reason FAILED and ERROR are separate outcomes: 1 means the suite
# ran and returned a verdict the repair loop can act on; 2-4 mean it never got that
# far, and 5 means nothing was collected — which is a broken invocation, not a pass.
_PYTEST_EXIT = {
    0: ExecutionOutcome.PASSED,
    1: ExecutionOutcome.FAILED,
    2: ExecutionOutcome.ERROR,  # interrupted
    3: ExecutionOutcome.ERROR,  # internal error
    4: ExecutionOutcome.ERROR,  # usage error
    5: ExecutionOutcome.ERROR,  # no tests collected
}

# The trailing status line, in both the banner form ("==== 1 failed, 2 passed in
# 0.12s ====") and the bare -q form ("2 passed in 0.01s"). Matched by the duration
# suffix, which both share and no other line has.
_DURATION_LINE = re.compile(r"\bin\s+\d+(?:\.\d+)?s\b")
_COUNT = re.compile(r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed|deselected)\b")

# "FAILED tests/test_calc.py::test_add - assert 1 == 2", from the short summary
# that ``run_pytest`` forces on with -rfE. A collection error has no "- message".
_SHORT_SUMMARY = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)(?:\s+-\s+(.*))?$")

# pytest-cov's terminal table footer: "TOTAL   123   12   90%".
_COVERAGE_TOTAL = re.compile(r"^TOTAL\s+.*?(\d+(?:\.\d+)?)%\s*$")

# "src/calc.py:3:1: F401 [*] `os` imported but unused" — ruff's default text output.
_RUFF_VIOLATION = re.compile(r"^(?P<loc>\S+:\d+:\d+):\s+(?P<message>.+)$")


class PytestCounts:
    """Counts and failing tests scraped from one pytest transcript."""

    __slots__ = ("passed", "failed", "errors", "skipped", "failures", "coverage_percent")

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.errors = 0
        self.skipped = 0
        self.failures: list[TestFailure] = []
        self.coverage_percent: float | None = None


def pytest_outcome(exit_code: int | None) -> ExecutionOutcome:
    """Map a pytest exit status to an outcome. ``None`` means it never exited."""
    if exit_code is None:
        return ExecutionOutcome.TIMEOUT
    return _PYTEST_EXIT.get(exit_code, ExecutionOutcome.ERROR)


def parse_pytest_output(text: str) -> PytestCounts:
    """Scrape counts, failing test ids and coverage out of a pytest transcript.

    Tolerant by construction: every field independently defaults to zero/None, so a
    truncated or unusual transcript degrades to "no detail" rather than raising and
    losing the run. ``-q`` and the default reporter are both handled.
    """
    counts = PytestCounts()
    summary_line: str | None = None

    for line in text.splitlines():
        stripped = line.strip()

        # Keep the *last* duration line: with -p no:cacheprovider or a plugin banner
        # an earlier line can look similar, and pytest's real total is always last.
        if _DURATION_LINE.search(stripped):
            summary_line = stripped

        match = _SHORT_SUMMARY.match(stripped)
        if match:
            test_id, message = match.group(1), (match.group(2) or "")
            if not any(f.test_id == test_id for f in counts.failures):
                counts.failures.append(TestFailure(test_id=test_id, message=message.strip()))

        coverage = _COVERAGE_TOTAL.match(stripped)
        if coverage:
            counts.coverage_percent = float(coverage.group(1))

    if summary_line:
        for number, label in _COUNT.findall(summary_line):
            value = int(number)
            # xfail/xpass are expectation bookkeeping, not a third kind of result:
            # folding them into skipped/passed keeps the four counts the repair loop
            # branches on from growing a long tail of pytest-specific cases.
            if label == "passed" or label == "xpassed":
                counts.passed += value
            elif label == "failed":
                counts.failed += value
            elif label.startswith("error"):
                counts.errors += value
            elif label in ("skipped", "xfailed", "deselected"):
                counts.skipped += value

    return counts


def parse_ruff_violations(text: str) -> list[TestFailure]:
    """ruff's text output → one entry per violation, shaped like a test failure.

    Same payload as a failing test on purpose: the repair loop (D8) then has one
    evidence format to consume and one prompt to write, instead of a lint-specific
    branch that would drift out of step with the test-specific one.
    """
    violations: list[TestFailure] = []
    for line in text.splitlines():
        match = _RUFF_VIOLATION.match(line.strip())
        if match:
            violations.append(
                TestFailure(test_id=match.group("loc"), message=match.group("message").strip())
            )
    return violations
