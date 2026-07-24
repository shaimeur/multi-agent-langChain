"""D8 — the repair loop: a broken function fixed autonomously, proven end to end.

The DoD lives in ``test_a_broken_function_is_repaired_in_under_three_iterations``.
Everything in that test is real except the model: a real git worktree, a real
``git apply``, a real pytest run in the real sandbox, and a real ``ExecutionReport``
driving the revision. The coder is scripted to get it wrong first and right second,
because what D8 has to prove is that *the loop converges on evidence* — that the
failing test ids come back to the editor and change what it does.

Model quality is the part a fake cannot prove, and it stays B2-gated (no cloud key).
That split is deliberate and is the same one D6 made: the mechanism is provable
offline today, the model's competence is not.
"""

from __future__ import annotations

import subprocess

import pytest
from langgraph.graph import END

from forge.config import CacheMode, Settings
from forge.core.agents.editor import make_editor_node
from forge.core.agents.reviewer import make_reviewer_node
from forge.core.loop import build_implement_loop
from forge.core.state import Budget
from forge.core.workspace import create_workspace, remove_workspace
from forge.models import (
    ChangePlan,
    Chunk,
    ChunkKind,
    CitationRef,
    ContextPack,
    ExecutionOutcome,
    ExecutionReport,
    GeneratedTest,
    Isolation,
    Patch,
    PatchSet,
    PlanStep,
    RevisionRequest,
    TestFailure,
)
from forge.tools.patch import apply_patchset

BUGGY = "def add(a, b):\n    return a - b  # bug\n"

REGRESSION_TEST = (
    "from calc import add\n\n\ndef test_add_returns_the_sum():\n    assert add(2, 3) == 5\n"
)


class ScriptedLLM:
    """Answers ``with_structured_output(S).invoke(...)`` from a per-schema queue.

    Keyed by schema name because one loop asks for two different shapes — a
    ``GeneratedTest`` from the tester and a ``PatchSet`` from the editor. The last
    item in a queue repeats, so a loop that runs longer than scripted still gets a
    well-formed answer rather than an IndexError dressed up as a model failure.
    """

    def __init__(self, **queues):
        self._queues = {name: list(items) for name, items in queues.items()}
        self.prompts: list[str] = []

    def with_structured_output(self, schema):
        return _Bound(self, schema.__name__)


class _Bound:
    def __init__(self, parent: ScriptedLLM, schema_name: str):
        self._parent = parent
        self._schema_name = schema_name

    def invoke(self, messages):
        self._parent.prompts.append("\n".join(str(m.content) for m in messages))
        queue = self._parent._queues[self._schema_name]
        return queue[0] if len(queue) == 1 else queue.pop(0)


@pytest.fixture
def settings(tmp_path, repo):
    return Settings(
        _env_file=None,
        cache_mode=CacheMode.REPLAY,
        target_repo=repo,
        workspace_root=tmp_path / "ws",
        sandbox_timeout_s=30,
    )


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "calc.py").write_text(BUGGY)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=root,
        check=True,
    )
    return root


@pytest.fixture
def workspace(settings, repo):
    ws = create_workspace("repair", settings=settings, repo=repo)
    yield ws
    remove_workspace(ws)


def _pack():
    """The retrieved context the plan cites. The reviewer's grounding point (§4/A5.1)
    resolves every citation against this, so the loop is given a real one."""
    return ContextPack(
        chunks=[
            Chunk(
                chunk_id="chunk-add",
                repo="repo",
                path="calc.py",
                language="python",
                kind=ChunkKind.FUNCTION,
                symbol="add",
                start_line=1,
                end_line=2,
                text="x",
                raw=BUGGY.strip(),
            )
        ]
    )


def _plan():
    return ChangePlan(
        summary="add() subtracts",
        steps=[
            PlanStep(
                intent="add() must return a + b",
                target_path="calc.py",
                evidence=[CitationRef(chunk_id="chunk-add", why="the buggy add")],
            )
        ],
    )


def _report(outcome=ExecutionOutcome.FAILED, **fields):
    return ExecutionReport(outcome=outcome, isolation=Isolation.DOCKER, exit_code=1, **fields)


# --- RevisionRequest: evidence, not a complaint ---------------------------


def test_a_revision_carries_the_failing_test_ids_from_the_report():
    report = _report(
        failed=2,
        failures=[
            TestFailure(test_id="tests/test_calc.py::test_add", message="assert -1 == 5"),
            TestFailure(test_id="tests/test_calc.py::test_sub"),
        ],
        stderr="Traceback: boom",
    )

    revision = RevisionRequest.from_report(report)

    assert [f.test_id for f in revision.failures] == [
        "tests/test_calc.py::test_add",
        "tests/test_calc.py::test_sub",
    ]
    evidence = revision.as_evidence()
    assert "tests/test_calc.py::test_add" in evidence
    assert "assert -1 == 5" in evidence, "the assertion is the actionable part"
    assert "Traceback: boom" in evidence


def test_a_timeout_is_described_as_a_timeout_not_as_failing_tests():
    """Routing a timeout back as 'tests failed' would send the editor after a
    non-existent assertion — the distinction has to survive into the revision."""
    revision = RevisionRequest.from_report(_report(outcome=ExecutionOutcome.TIMEOUT))

    assert "deadline" in revision.reason
    assert revision.failures == []


# --- applying to the worktree ---------------------------------------------


def test_apply_patchset_writes_into_the_worktree(workspace):
    patchset = PatchSet(
        patches=[Patch(path="calc.py", old_string="return a - b  # bug", new_string="return a + b")]
    )

    result = apply_patchset(workspace, patchset)

    assert result.ok is True, result.message
    assert "return a + b" in workspace.read("calc.py")


def test_apply_patchset_refuses_a_patch_that_does_not_check_out(workspace):
    """The dry run gates the write — nothing unverified reaches disk."""
    patchset = PatchSet(
        patches=[Patch(path="calc.py", old_string="text that is not there", new_string="x")]
    )

    result = apply_patchset(workspace, patchset)

    assert result.ok is False
    assert workspace.read("calc.py") == BUGGY, "the worktree is untouched by a refused patch"


def test_applying_never_touches_the_source_repo(workspace, repo):
    apply_patchset(
        workspace,
        PatchSet(
            patches=[
                Patch(path="calc.py", old_string="return a - b  # bug", new_string="return a + b")
            ]
        ),
    )

    assert (repo / "calc.py").read_text() == BUGGY


# --- the reviewer's routing -----------------------------------------------


def _reviewed(settings, **state):
    """Run the reviewer node over a well-formed state — plan and pack included,
    because the grounding point resolves the plan's citations against the pack."""
    node = make_reviewer_node(settings=settings)
    return node({"plan": _plan(), "pack": _pack(), "patch_ok": True, "iterations": 0, **state})


def test_reviewer_approves_a_green_run(settings):
    command = _reviewed(settings, report=_report(ExecutionOutcome.PASSED, passed=3))

    assert command.goto == END
    assert command.update["review"].approved
    assert command.update["revision"] is None


def test_reviewer_sends_a_red_run_back_to_the_editor(settings):
    command = _reviewed(settings, report=_report(failed=1, failures=[TestFailure(test_id="t::a")]))

    assert command.goto == "editor"
    assert command.update["revision"].failures[0].test_id == "t::a"


def test_reviewer_stops_at_the_iteration_cap_instead_of_looping(settings):
    """A model that cannot fix it stops costing money — cahier §9, the A0 guard."""
    command = _reviewed(
        settings, report=_report(failed=1), iterations=settings.max_iterations_per_step - 1
    )

    assert command.goto == END
    assert command.update["iterations"] == settings.max_iterations_per_step
    assert command.update["halted"], "it stops with a readable reason, not silently"


def test_a_patch_that_did_not_apply_is_its_own_revision_reason(settings):
    command = _reviewed(settings, report=_report(ExecutionOutcome.PASSED), patch_ok=False)

    assert command.goto == "editor"
    assert "did not apply" in command.update["revision"].reason


# --- the editor end of the loop -------------------------------------------


def test_the_editor_is_shown_the_previous_failure_as_evidence(workspace):
    llm = ScriptedLLM(
        PatchSet=[
            PatchSet(
                patches=[
                    Patch(
                        path="calc.py",
                        old_string="return a - b  # bug",
                        new_string="return a + b",
                    )
                ]
            )
        ]
    )
    node = make_editor_node(llm=llm, workspace=workspace)

    node(
        {
            "plan": _plan(),
            "budget": Budget(),
            "revision": RevisionRequest(
                reason="1 test(s) still failing",
                failures=[TestFailure(test_id="tests/t.py::test_add", message="assert 6 == 5")],
            ),
        }
    )

    assert "tests/t.py::test_add" in llm.prompts[0]
    assert "assert 6 == 5" in llm.prompts[0]


def test_the_revision_is_consumed_so_the_next_pass_starts_clean(workspace):
    """A stale revision would have the editor fixing a failure that no longer exists."""
    llm = ScriptedLLM(
        PatchSet=[
            PatchSet(
                patches=[
                    Patch(
                        path="calc.py", old_string="return a - b  # bug", new_string="return a + b"
                    )
                ]
            )
        ]
    )
    node = make_editor_node(llm=llm, workspace=workspace)

    out = node({"plan": _plan(), "budget": Budget(), "revision": RevisionRequest(reason="x")})

    assert out["revision"] is None


# --- the DoD --------------------------------------------------------------


def test_a_broken_function_is_repaired_in_under_three_iterations(workspace, settings, repo):
    """D8's DoD. The only fake is the model; the tests really run in the sandbox.

    The coder is scripted to get it wrong first (`a * b`) and right second (`a + b`),
    so a loop that did not feed the failure back would stop at the wrong answer.
    """
    llm = ScriptedLLM(
        GeneratedTest=[GeneratedTest(path="tests/test_add.py", source=REGRESSION_TEST)],
        PatchSet=[
            # Attempt 1 — wrong. The regression test stays red.
            PatchSet(
                patches=[
                    Patch(
                        path="calc.py", old_string="return a - b  # bug", new_string="return a * b"
                    )
                ]
            ),
            # Attempt 2 — right, and anchored to what attempt 1 actually left on disk.
            PatchSet(
                patches=[
                    Patch(path="calc.py", old_string="return a * b", new_string="return a + b")
                ]
            ),
        ],
    )

    loop = build_implement_loop(coder_llm=llm, workspace=workspace, settings=settings)
    final = loop.invoke({"plan": _plan(), "pack": _pack(), "budget": Budget(), "iterations": 0})

    assert final["regression_red"] is True, "the regression test must fail before the fix"
    assert final["report"].ok, f"the suite is green: {final['report'].headline()}"
    assert final["iterations"] < 3, f"took {final['iterations']} iterations"
    assert final["patch_ok"] is True

    # The repair is real: the worktree holds the fix and the pinned repo does not.
    assert "return a + b" in workspace.read("calc.py")
    assert (repo / "calc.py").read_text() == BUGGY


def test_the_loop_gives_up_cleanly_when_the_model_never_fixes_it(workspace, settings):
    """The budget guard, end to end: three bad patches stop, they do not spin."""
    never_fixes = ScriptedLLM(
        GeneratedTest=[GeneratedTest(path="tests/test_add.py", source=REGRESSION_TEST)],
        PatchSet=[
            PatchSet(
                patches=[Patch(path="calc.py", old_string="  # bug", new_string="  # still broken")]
            )
        ],
    )

    loop = build_implement_loop(coder_llm=never_fixes, workspace=workspace, settings=settings)
    final = loop.invoke({"plan": _plan(), "pack": _pack(), "budget": Budget(), "iterations": 0})

    assert final["iterations"] == settings.max_iterations_per_step
    assert not final["report"].ok
    assert final["report"].failures, "it stops with the evidence intact, not empty-handed"


def test_a_regression_test_that_passes_before_the_fix_is_reported_as_such(workspace, settings):
    """A green 'regression' test pins nothing — the loop must not call that success."""
    useless_test = "def test_nothing():\n    assert True\n"
    llm = ScriptedLLM(
        GeneratedTest=[GeneratedTest(path="tests/test_nothing.py", source=useless_test)],
        PatchSet=[
            PatchSet(
                patches=[
                    Patch(
                        path="calc.py", old_string="return a - b  # bug", new_string="return a + b"
                    )
                ]
            )
        ],
    )

    loop = build_implement_loop(coder_llm=llm, workspace=workspace, settings=settings)
    final = loop.invoke({"plan": _plan(), "pack": _pack(), "budget": Budget(), "iterations": 0})

    assert final["regression_red"] is False
