"""D9 — the reviewer's five points and the two human gates.

The DoD is ``test_the_full_graph_runs_headless_through_both_approval_points``: the
whole change path, planner included, stopping twice for a human and resuming both
times through ``Command(resume=...)``.

The point the rest of the file defends is *who decides what*. Three of the five
checklist points never reach a model, and the tests that matter here are the ones
where a model tries to say otherwise and is ignored — a reviewer that could be talked
out of a red suite would make every other guarantee in FORGE decorative.
"""

from __future__ import annotations

import subprocess

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END
from langgraph.types import Command

from forge.config import CacheMode, Settings
from forge.core.agents.base import latest_user_text
from forge.core.agents.reviewer import (
    check_grounding,
    check_security,
    check_tests,
    make_reviewer_node,
    review,
)
from forge.core.approval import parse_decision
from forge.core.checkpoint import forge_serde
from forge.core.loop import build_change_graph
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
    ReviewCheckId,
    ReviewJudgement,
    Verdict,
)
from tests.test_repair_loop import BUGGY, REGRESSION_TEST, ScriptedLLM

# --- fixtures -------------------------------------------------------------


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
def settings(tmp_path, repo):
    return Settings(
        _env_file=None,
        cache_mode=CacheMode.REPLAY,
        target_repo=repo,
        workspace_root=tmp_path / "ws",
        sandbox_timeout_s=30,
    )


@pytest.fixture
def workspace(settings, repo):
    ws = create_workspace("d9", settings=settings, repo=repo)
    yield ws
    remove_workspace(ws)


def _pack():
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


def _plan(blast_radius=None):
    return ChangePlan(
        summary="add() subtracts",
        steps=[
            PlanStep(
                intent="add() must return a + b",
                target_path="calc.py",
                evidence=[CitationRef(chunk_id="chunk-add", why="the buggy add")],
            )
        ],
        blast_radius=blast_radius or [],
    )


def _fix():
    return PatchSet(
        patches=[Patch(path="calc.py", old_string="return a - b  # bug", new_string="return a + b")]
    )


def _report(outcome=ExecutionOutcome.PASSED, **fields):
    fields.setdefault("passed", 1)
    return ExecutionReport(
        outcome=outcome,
        isolation=Isolation.DOCKER,
        exit_code=0 if outcome == "passed" else 1,
        **fields,
    )


# --- point 1: grounding, in code ------------------------------------------


def test_grounding_passes_when_every_step_cites_a_retrieved_chunk():
    check = check_grounding(_plan(), _pack())

    assert check.passed
    assert check.programmatic is True


def test_grounding_fails_on_a_citation_that_was_never_retrieved():
    plan = _plan()
    plan.steps[0].evidence = [CitationRef(chunk_id="never-retrieved")]

    check = check_grounding(plan, _pack())

    assert not check.passed
    assert "never retrieved" in check.justification


def test_a_plan_with_no_steps_is_not_grounded():
    assert not check_grounding(ChangePlan(), _pack()).passed


# --- point 3: tests, read not argued --------------------------------------


@pytest.mark.parametrize(
    "report, passes, why",
    [
        (None, False, "never run"),
        (
            ExecutionReport(outcome=ExecutionOutcome.TIMEOUT, isolation=Isolation.DOCKER),
            False,
            "deadline",
        ),
        (
            ExecutionReport(outcome=ExecutionOutcome.ERROR, isolation=Isolation.DOCKER),
            False,
            "could not run",
        ),
        (
            ExecutionReport(outcome=ExecutionOutcome.PASSED, isolation=Isolation.DOCKER, passed=0),
            False,
            "no tests at all",
        ),
        (
            ExecutionReport(outcome=ExecutionOutcome.PASSED, isolation=Isolation.DOCKER, passed=3),
            True,
            "ran and passed",
        ),
    ],
)
def test_the_tests_point_reads_the_report(report, passes, why):
    check = check_tests(report)

    assert check.passed is passes
    assert why in check.justification
    assert check.programmatic is True


def test_a_green_exit_that_collected_nothing_does_not_count_as_passing():
    """The failure mode this point exists for: a 'fix' that deletes the tests."""
    vacuous = ExecutionReport(
        outcome=ExecutionOutcome.PASSED, isolation=Isolation.DOCKER, exit_code=0
    )

    assert not check_tests(vacuous).passed


# --- point 4: security, in code -------------------------------------------


@pytest.mark.parametrize(
    "new_string, expected",
    [
        ('api_key = "sk-abcdefghijklmnopqrstuvwx"', "credential"),
        ("result = eval(user_input)", "eval/exec"),
        ("import subprocess", "network call or subprocess"),
        ("value = 1", ""),
    ],
)
def test_the_security_point_flags_the_prohibited_patterns(new_string, expected):
    patchset = PatchSet(
        patches=[Patch(path="calc.py", old_string="value = 0", new_string=new_string)]
    )

    check = check_security(patchset)

    assert check.programmatic is True
    if expected:
        assert not check.passed
        assert expected in check.justification
    else:
        assert check.passed


def test_deleting_a_test_is_a_security_failure():
    patchset = PatchSet(
        patches=[
            Patch(
                path="tests/test_calc.py",
                old_string="def test_add():\n    assert add(1, 1) == 2",
                new_string="",
            )
        ]
    )

    assert "test deleted" in check_security(patchset).justification


def test_an_unchanged_line_carried_through_a_patch_is_not_flagged():
    """A search/replace block carries context; flagging it would make point 4 useless."""
    patchset = PatchSet(
        patches=[
            Patch(
                path="calc.py",
                old_string="import subprocess\nx = 1",
                new_string="import subprocess\nx = 2",
            )
        ]
    )

    assert check_security(patchset).passed


# --- the model cannot reach the programmatic points -----------------------


def test_a_model_claiming_success_cannot_override_a_red_suite():
    """The whole design in one test. The reviewer model votes yes on both of its
    points; the suite is red; the verdict is still REVISE."""
    enthusiastic = ScriptedLLM(
        ReviewJudgement=[
            ReviewJudgement(
                plan_conformance=True,
                plan_conformance_why="looks perfect to me",
                regression_risk_ok=True,
                regression_risk_why="no risk at all",
            )
        ]
    )

    verdict = review(
        plan=_plan(),
        pack=_pack(),
        patchset=_fix(),
        report=_report(ExecutionOutcome.FAILED, passed=0, failed=2),
        llm=enthusiastic,
    )

    assert verdict.verdict is Verdict.REVISE
    tests_check = next(c for c in verdict.checks if c.check is ReviewCheckId.TESTS)
    assert not tests_check.passed
    assert tests_check.programmatic is True


def test_the_reviewer_model_is_only_asked_for_two_points():
    """It is handed a schema with no field for grounding, tests or security."""
    assert set(ReviewJudgement.model_fields) == {
        "plan_conformance",
        "plan_conformance_why",
        "regression_risk_ok",
        "regression_risk_why",
    }


def test_a_reviewer_model_that_crashes_does_not_approve_by_default():
    class Exploding:
        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            raise RuntimeError("provider down")

    verdict = review(plan=_plan(), pack=_pack(), patchset=_fix(), report=_report(), llm=Exploding())

    assert verdict.verdict is Verdict.REVISE
    assert any("provider down" in c.justification for c in verdict.failed())


def test_without_a_reviewer_model_the_two_judged_points_say_so():
    """Silence is not assent — an unasked question must not read as a satisfied one."""
    verdict = review(plan=_plan(), pack=_pack(), patchset=_fix(), report=_report(), llm=None)

    judged = [c for c in verdict.checks if not c.programmatic]
    assert len(judged) == 2
    assert all("not evaluated" in c.justification for c in judged)


def test_all_five_points_are_always_reported():
    verdict = review(plan=_plan(), pack=_pack(), patchset=_fix(), report=_report(), llm=None)

    assert {c.check for c in verdict.checks} == set(ReviewCheckId)


# --- the resume value is untrusted input ----------------------------------


@pytest.mark.parametrize(
    "value, approved",
    [
        (True, True),
        (False, False),
        ("approve", True),
        ("reject", False),
        ({"approved": True}, True),
        ({"approved": False, "feedback": "no"}, False),
        (None, False),
        ("banana", False),
        (12345, False),
    ],
)
def test_only_an_explicit_yes_counts_as_approval(value, approved):
    """It arrives from an API caller. Anything unrecognised must not mean 'go ahead'."""
    assert parse_decision(value)[0] is approved


# --- loop pathology and graceful stops ------------------------------------


def test_three_disagreements_on_one_file_escalate_instead_of_looping(settings):
    node = make_reviewer_node(settings=settings)

    command = node(
        {
            "plan": _plan(),
            "pack": _pack(),
            "report": _report(ExecutionOutcome.FAILED, passed=0, failed=1),
            "patch_ok": True,
            "iterations": 0,
            "contested": {"calc.py": settings.max_iterations_per_step - 1},
        }
    )

    assert command.goto == "escalate"


def test_budget_exhaustion_halts_with_a_sentence_not_a_traceback(settings):
    node = make_reviewer_node(settings=settings)

    command = node(
        {
            "plan": _plan(),
            "pack": _pack(),
            "report": _report(ExecutionOutcome.FAILED, passed=0, failed=1),
            "patch_ok": True,
            "iterations": 0,
            "budget": Budget(llm_calls=settings.max_llm_calls_per_run),
        }
    )

    assert command.goto == END
    assert "cap reached" in command.update["halted"]


# --- the DoD --------------------------------------------------------------


def _scripted_run():
    """A planner, a coder and a reviewer, all scripted — the graph is what is under test."""
    return ScriptedLLM(
        ChangePlan=[_plan()],
        GeneratedTest=[GeneratedTest(path="tests/test_add.py", source=REGRESSION_TEST)],
        PatchSet=[_fix()],
        ReviewJudgement=[
            ReviewJudgement(
                plan_conformance=True,
                plan_conformance_why="the patch changes only the reported line",
                regression_risk_ok=True,
                regression_risk_why="nothing else was in scope",
            )
        ],
    )


def test_the_full_graph_runs_headless_through_both_approval_points(workspace, settings, repo):
    """D9's DoD — planner → plan gate → loop → patch gate → green, resumed twice."""
    llm = _scripted_run()
    graph = build_change_graph(
        planner_llm=llm,
        coder_llm=llm,
        reviewer_llm=llm,
        workspace=workspace,
        settings=settings,
        checkpointer=MemorySaver(serde=forge_serde()),
    )
    config = {"configurable": {"thread_id": "d9-dod"}}

    # 1 — runs until the plan needs a human, and stops there.
    first = graph.invoke({"pack": _pack(), "budget": Budget(), "iterations": 0}, config=config)
    assert first["__interrupt__"], "the graph must stop for plan approval"
    assert first["__interrupt__"][0].value["kind"] == "plan_approval"
    assert "return a - b" in workspace.read("calc.py"), "nothing was changed before approval"

    # 2 — approve the plan; it runs on until the patch needs a human.
    second = graph.invoke(Command(resume={"approved": True}), config=config)
    assert second["__interrupt__"][0].value["kind"] == "patch_approval"
    diff = second["__interrupt__"][0].value["diff"]
    assert "return a + b" in diff, "the human is shown the actual diff"
    assert "return a - b" in workspace.read("calc.py"), "still nothing written to disk"

    # 3 — approve the patch; it applies, tests, reviews and finishes.
    final = graph.invoke(Command(resume={"approved": True}), config=config)

    assert final.get("__interrupt__") is None, "the run completed"
    assert final["approvals"] == ["plan:approved", "patch:approved"]
    assert final["review"].approved, final["review"].feedback
    assert final["report"].ok
    assert final["regression_red"] is True
    assert "return a + b" in workspace.read("calc.py")
    assert (repo / "calc.py").read_text() == BUGGY, "the pinned repo is untouched"


def test_rejecting_the_plan_stops_before_anything_is_written(workspace, settings):
    llm = _scripted_run()
    graph = build_change_graph(
        planner_llm=llm,
        coder_llm=llm,
        reviewer_llm=llm,
        workspace=workspace,
        settings=settings,
        checkpointer=MemorySaver(serde=forge_serde()),
    )
    config = {"configurable": {"thread_id": "d9-reject-plan"}}

    graph.invoke({"pack": _pack(), "budget": Budget(), "iterations": 0}, config=config)
    final = graph.invoke(
        Command(resume={"approved": False, "feedback": "wrong file"}), config=config
    )

    assert final["approvals"] == ["plan:rejected"]
    assert "wrong file" in final["halted"]
    assert workspace.read("calc.py") == BUGGY


def test_forge_payloads_survive_the_strict_checkpoint_serializer():
    """The interrupt/resume path round-trips state through the checkpointer, and the
    default serializer would rebuild *any* named type — a code-execution vector for
    anyone who can write to checkpoints.sqlite. Strict mode plus an allowlist keeps
    the round-trip working (C4, D9 resume) without keeping the hole."""
    import warnings

    serde = forge_serde()
    payloads = [
        _plan(),
        _pack(),
        _fix(),
        _report(),
        Budget(llm_calls=2),
    ]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        restored = [serde.loads_typed(serde.dumps_typed(p)) for p in payloads]

    assert [type(r) for r in restored] == [type(p) for p in payloads]
    assert not [w for w in caught if "unregistered type" in str(w.message)]


def test_a_type_outside_forge_is_not_reconstructed_from_a_checkpoint():
    """The allowlist is scoped to FORGE's own modules, not to 'anything importable'."""
    import argparse

    with pytest.raises(TypeError):
        forge_serde().dumps_typed(argparse.Namespace(hostile=True))


def test_rejecting_the_patch_needs_no_rollback(workspace, settings):
    """The gate is before the write, so 'no' is simply the absence of a change."""
    llm = _scripted_run()
    graph = build_change_graph(
        planner_llm=llm,
        coder_llm=llm,
        reviewer_llm=llm,
        workspace=workspace,
        settings=settings,
        checkpointer=MemorySaver(serde=forge_serde()),
    )
    config = {"configurable": {"thread_id": "d9-reject-patch"}}

    graph.invoke({"pack": _pack(), "budget": Budget(), "iterations": 0}, config=config)
    graph.invoke(Command(resume={"approved": True}), config=config)
    final = graph.invoke(Command(resume={"approved": False}), config=config)

    assert final["approvals"] == ["plan:approved", "patch:rejected"]
    assert workspace.read("calc.py") == BUGGY, "the worktree never changed"


# --- the retriever entry --------------------------------------------------


def test_the_change_graph_retrieves_before_it_plans(workspace, settings):
    """A run that starts from a bare request reaches the planner already grounded.

    The regression this pins: `forge fix` and the API built this graph with no
    retriever, so the planner opened on an empty ``<context></context>``, answered
    needs_more_context by construction, and the run dead-ended at END having written
    nothing — the observed failure in both recorded web-UI fixtures. Entering at the
    planner even *with* a retriever wired is only marginally better: it spends a model
    call to discover what the graph already knew, and on a rate-limited key that call
    is the scarce resource.
    """
    llm = _scripted_run()
    seen: list[str] = []

    def spy_retriever(state):
        seen.append(latest_user_text(state))
        return {"pack": _pack()}

    graph = build_change_graph(
        planner_llm=llm,
        coder_llm=llm,
        reviewer_llm=llm,
        workspace=workspace,
        settings=settings,
        checkpointer=MemorySaver(serde=forge_serde()),
        retriever_node=spy_retriever,
    )
    config = {"configurable": {"thread_id": "retrieve-first"}}

    # No pack in the input — exactly how the CLI and the API start a run.
    first = graph.invoke(
        {
            "messages": [HumanMessage(content="add() subtracts")],
            "budget": Budget(),
            "iterations": 0,
        },
        config=config,
    )

    assert seen == ["add() subtracts"], "the retriever ran first, once, on the request"
    assert first["__interrupt__"][0].value["kind"] == "plan_approval", "it reached the plan gate"
    assert llm.prompts, "the planner was called"
    assert "<context>\n\n</context>" not in llm.prompts[0], "the planner never saw an empty pack"
    assert BUGGY.strip() in llm.prompts[0], "the retrieved code was in the planner's first prompt"
