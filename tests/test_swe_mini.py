"""D8 — the seeded-bug benchmark is itself tested, because a silent one is worthless.

A benchmark can rot in a way that flatters the agent: a target-repo bump changes a
line, the seed patch stops applying, and every bug scores as "already fixed". So each
bug is checked here for the three properties that make it mean anything — the hidden
test is green on clean code, red once the bug is seeded, and green again after the
reference fix.

Skipped when ``data/target`` is absent: the pinned sqlparse clone is git-ignored
(ADR-003), so a fresh checkout has no repo to seed until `forge index` sets one up.
"""

from __future__ import annotations

import pytest

from evals.swe_mini.bugs import BUGS, by_id
from evals.swe_mini.harness import grade, run_hidden_test, seed, verify
from forge.config import CacheMode, Settings, get_settings
from forge.core.workspace import session_workspace
from forge.models import ExecutionOutcome

_TARGET = get_settings().target_repo
pytestmark = pytest.mark.skipif(
    not (_TARGET / ".git").is_dir(),
    reason=f"the pinned target clone is absent at {_TARGET} (git-ignored, see ADR-003)",
)


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        cache_mode=CacheMode.REPLAY,
        target_repo=_TARGET,
        workspace_root=tmp_path / "ws",
        sandbox_timeout_s=60,
    )


def test_there_are_four_bugs():
    """descope §7 cut the cahier's ten to four. The harness runs N; this is N."""
    assert len(BUGS) == 4
    assert len({b.bug_id for b in BUGS}) == 4, "bug ids must be unique"


@pytest.mark.parametrize("bug", BUGS, ids=[b.bug_id for b in BUGS])
def test_each_bug_is_seedable_detectable_and_fixable(bug, settings):
    """The one property the benchmark rests on, per bug."""
    with session_workspace(f"t-{bug.bug_id}", settings=settings) as workspace:
        problem = verify(workspace, bug, settings=settings)

    assert not problem, f"{bug.bug_id} is unsound: {problem}"


def test_the_hidden_test_is_not_left_on_disk(settings):
    """The agent must never see the test it is graded by."""
    bug = by_id("SM-01")
    with session_workspace("t-hidden", settings=settings) as workspace:
        run_hidden_test(workspace, bug, settings=settings)

        assert not workspace.exists(bug.hidden_test_path)


def test_grading_a_seeded_but_unrepaired_bug_reports_failure(settings):
    """The grader's null result: no fix attempted, so nothing is repaired."""
    bug = by_id("SM-02")
    with session_workspace("t-unfixed", settings=settings) as workspace:
        seed(workspace, bug)
        result = grade(workspace, bug, settings=settings)

    assert result.repaired is False
    assert result.status == "FAILED"


def test_grading_the_reference_fix_reports_a_repair(settings):
    """...and its positive counterpart, so a grader stuck on False cannot pass."""
    from forge.tools.patch import apply_patchset

    bug = by_id("SM-02")
    with session_workspace("t-fixed", settings=settings) as workspace:
        seed(workspace, bug)
        assert apply_patchset(workspace, bug.repair_patchset()).ok
        result = grade(workspace, bug, settings=settings)

    assert result.repaired is True
    assert result.regressions is False, "the reference fix must not break the rest of the suite"
    assert result.status == "REPAIRED"


def test_a_seeded_bug_actually_breaks_the_projects_own_suite(settings):
    """Extra evidence the bugs are realistic: sqlparse's own tests catch them too.

    Not the grading rule — the hidden test is — but a seeded bug that the upstream
    suite is entirely happy with would be a sign the behaviour is not really used.
    """
    bug = by_id("SM-01")
    with session_workspace("t-upstream", settings=settings) as workspace:
        seed(workspace, bug)
        from forge.sandbox.tools import run_pytest

        report = run_pytest(workspace, ["tests"], settings=settings)

    assert report.outcome is ExecutionOutcome.FAILED
    assert report.failed > 0
