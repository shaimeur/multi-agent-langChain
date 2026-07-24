"""PLANNER + EDITOR — a change request becomes a plan and a git-appliable patch.

The DoD lives in ``test_change_request_yields_a_patch_git_accepts``: fake planner and
editor, a real worktree, and ``git apply --check`` as the judge — no network, no
weights. Grounding (a step must cite retrieved evidence) and the re-entry cap are
enforced here too, in code.
"""

from __future__ import annotations

import subprocess

import pytest
from langgraph.graph import END

from forge.config import CacheMode, Settings
from forge.core.agents.editor import edit_step, make_editor_node
from forge.core.agents.planner import make_planner_node, plan_change
from forge.core.state import Budget
from forge.core.workspace import create_workspace, remove_workspace
from forge.models import (
    ChangePlan,
    Chunk,
    ChunkKind,
    CitationRef,
    ContextPack,
    Patch,
    PatchSet,
    PlanStep,
)
from forge.tools.patch import apply_patch_dryrun

BUGGY = "def add(a, b):\n    return a - b  # bug\n"


class FakeStructured:
    """A structured-output stand-in returning a fixed object."""

    def __init__(self, value):
        self._value = value

    def with_structured_output(self, schema):
        return self

    def invoke(self, messages):
        return self._value


@pytest.fixture
def workspace(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "calc.py").write_text(BUGGY)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"],
        cwd=repo,
        check=True,
    )
    settings = Settings(
        _env_file=None,
        cache_mode=CacheMode.REPLAY,
        target_repo=repo,
        workspace_root=tmp_path / "ws",
    )
    ws = create_workspace("chg", settings=settings, repo=repo)
    yield ws
    remove_workspace(ws)


@pytest.fixture
def pack():
    chunk = Chunk(
        chunk_id="chunk-add",
        repo="repo",
        path="src/calc.py",
        language="python",
        kind=ChunkKind.FUNCTION,
        symbol="add",
        start_line=1,
        end_line=2,
        text="x",
        raw=BUGGY.strip(),
    )
    return ContextPack(chunks=[chunk])


def _step(evidence_id="1"):
    return PlanStep(
        intent="add() subtracts; return a + b",
        target_path="src/calc.py",
        evidence=[CitationRef(chunk_id=evidence_id, why="the buggy add")],
    )


def _fix_patchset():
    return PatchSet(
        patches=[
            Patch(path="src/calc.py", old_string="return a - b  # bug", new_string="return a + b")
        ]
    )


# --- planner grounding ----------------------------------------------------


def test_plan_change_remaps_cited_snippet_numbers_to_chunk_ids(pack):
    plan = ChangePlan(steps=[_step(evidence_id="1")])
    grounded = plan_change(FakeStructured(plan), "fix add", pack)
    assert grounded.steps[0].evidence[0].chunk_id == "chunk-add"
    assert grounded.steps[0].is_grounded(pack)


def test_a_step_citing_an_unretrieved_chunk_is_ungrounded(pack):
    plan = ChangePlan(steps=[_step(evidence_id="99")])  # no 99th snippet
    checked = plan_change(FakeStructured(plan), "fix add", pack)
    assert checked.ungrounded_steps(pack) == checked.steps  # every step rejected


def test_a_step_with_no_evidence_is_ungrounded(pack):
    step = PlanStep(intent="do it", target_path="src/calc.py", evidence=[])
    assert step.is_grounded(pack) is False


# --- planner routing ------------------------------------------------------


def test_planner_routes_a_grounded_plan_to_the_editor(pack):
    node = make_planner_node(llm=FakeStructured(ChangePlan(steps=[_step()])))
    cmd = node({"messages": [], "pack": pack, "budget": Budget(), "plan_reentries": 0})
    assert cmd.goto == "editor"
    assert cmd.update["plan"].steps[0].is_grounded(pack)


def test_planner_asks_the_retriever_for_more_context_then_caps(pack):
    # A plan that needs more context routes back to the retriever, once.
    node = make_planner_node(llm=FakeStructured(ChangePlan(needs_more_context=True, missing="x")))
    first = node({"messages": [], "pack": pack, "budget": Budget(), "plan_reentries": 0})
    assert first.goto == "retriever" and first.update["plan_reentries"] == 1
    # At the cap, it stops instead of looping — no grounded steps → END.
    second = node({"messages": [], "pack": pack, "budget": Budget(), "plan_reentries": 1})
    assert second.goto == END


# --- editor ---------------------------------------------------------------


def test_edit_step_reads_the_worktree_and_anchors_a_blank_path(workspace):
    # A model that forgets the path still gets it anchored to the step's target.
    patchset = PatchSet(
        patches=[Patch(path="", old_string="return a - b  # bug", new_string="return a + b")]
    )
    result = edit_step(FakeStructured(patchset), _step(), workspace)
    assert result.patches[0].path == "src/calc.py"


def test_editor_node_reports_a_dry_run_result(workspace, pack):
    plan = ChangePlan(summary="fix", steps=[_step()])
    plan.steps[0].evidence[0].chunk_id = "chunk-add"
    node = make_editor_node(llm=FakeStructured(_fix_patchset()), workspace=workspace)
    out = node({"plan": plan, "budget": Budget()})
    assert out["patch_ok"] is True
    assert out["patchset"].patches


# --- the DoD --------------------------------------------------------------


def test_change_request_yields_a_patch_git_accepts(workspace, pack):
    """A plan and a patch that `git apply --check` accepts — not yet executed."""
    plan = plan_change(
        FakeStructured(ChangePlan(summary="fix add", steps=[_step("1")])), "fix add", pack
    )
    assert not plan.ungrounded_steps(pack), "every planned step must be grounded"

    patchset = edit_step(FakeStructured(_fix_patchset()), plan.steps[0], workspace)
    result = apply_patch_dryrun(workspace, patchset)

    assert result.ok is True, result.message
    assert "return a + b" in result.diff
    # never executed: the worktree file is unchanged by the dry run
    assert "return a - b  # bug" in workspace.read("src/calc.py")
