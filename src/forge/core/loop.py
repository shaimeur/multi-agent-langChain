"""The ``implement_loop`` subgraph — write, run, judge, revise (cahier §4, §5.1).

    START → regression → editor → verify → reviewer → { editor | END }
                                              ↑__________|  capped

``regression`` writes the failing test once, before any patch. ``editor`` turns the
plan step into a ``PatchSet`` and applies it to the session worktree. ``verify`` runs
the suite in the sandbox. ``reviewer`` reads the ``ExecutionReport`` and either
approves or sends a ``RevisionRequest`` back to the editor. The cycle is bounded by
``max_iterations_per_step``, so a model that cannot fix something stops costing money
instead of looping until the budget dies.

Two properties are worth stating because they are what make the loop trustworthy
rather than merely automated:

**The judgement is not a model's.** The reviewer at D8 is a deterministic stub that
routes on the exit code the sandbox reported. D9 replaces it with the real 5-point
reviewer, but even then the "did the tests pass" half stays in code — an LLM is never
asked to decide whether its own patch worked.

**The editor still never writes.** It emits structured edits; ``apply_patchset``
dry-runs ``git apply --check`` and only then writes, into the per-session worktree
that is thrown away at the end. D9 puts the human approval interrupt in front of it.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from forge.config import Settings, get_settings
from forge.core.agents.base import get_budget, get_plan
from forge.core.agents.editor import edit_step
from forge.core.agents.tester import make_regression_node, make_verify_node
from forge.core.state import ForgeState
from forge.core.workspace import Workspace
from forge.models import ChangePlan, ExecutionReport, PatchSet, RevisionRequest
from forge.tools.patch import apply_patchset


def _get_report(state: ForgeState) -> ExecutionReport | None:
    """A checkpoint can hand the report back as a dict — normalise either form."""
    report = state.get("report")
    if report is None or isinstance(report, ExecutionReport):
        return report
    return ExecutionReport(**report) if isinstance(report, dict) else None


def _get_revision(state: ForgeState) -> RevisionRequest | None:
    revision = state.get("revision")
    if revision is None or isinstance(revision, RevisionRequest):
        return revision
    return RevisionRequest(**revision) if isinstance(revision, dict) else None


def make_apply_editor_node(
    *, llm: BaseChatModel, workspace: Workspace
) -> Callable[[ForgeState], dict]:
    """EDITOR + apply. The loop's writing end.

    Distinct from D6's ``make_editor_node``, which dry-runs and stops: that one is
    the "show me the diff" path, this one is the "and now run the tests on it" path.
    Both go through the same ``git apply --check`` gate first.
    """

    def editor_node(state: ForgeState) -> dict:
        plan = get_plan(state) or ChangePlan()
        revision = _get_revision(state)
        budget = get_budget(state)
        if not plan.steps:
            return {"patch_ok": False, "budget": budget}

        step = plan.steps[min(revision.target_step if revision else 0, len(plan.steps) - 1)]
        patchset = edit_step(llm, step, workspace, revision)
        result = apply_patchset(workspace, patchset)

        return {
            "patchset": PatchSet(summary=plan.summary, patches=patchset.patches),
            "patch_ok": result.ok,
            "budget": budget.spend(calls=1),
            # Consumed: the next editor entry gets a fresh one from the reviewer, and
            # a stale revision would have it fixing a failure that no longer exists.
            "revision": None,
        }

    return editor_node


def make_reviewer_stub_node(*, settings: Settings | None = None) -> Callable[[ForgeState], Command]:
    """The D8 reviewer: route on the sandbox's exit code, nothing else.

    A stub in the sense that it has no opinion about style, scope or security — D9
    adds those. It is *not* a stub in the sense of being fake: the approve/revise
    decision it makes here is the real one, made from the real report, and D9's
    reviewer keeps this check and adds to it.
    """
    settings = settings or get_settings()

    def reviewer_node(state: ForgeState) -> Command:
        report = _get_report(state)
        iterations = state.get("iterations", 0) + 1

        if report is not None and report.ok and state.get("patch_ok"):
            return Command(goto=END, update={"iterations": iterations, "revision": None})

        if iterations >= settings.max_iterations_per_step:
            # Out of attempts. Stop with the evidence intact rather than looping —
            # a partial, explained failure is the graceful degradation of cahier §9.
            return Command(goto=END, update={"iterations": iterations})

        if report is None:
            revision = RevisionRequest(reason="no test report was produced")
        elif not state.get("patch_ok"):
            revision = RevisionRequest(reason="the patch did not apply to the worktree")
        else:
            revision = RevisionRequest.from_report(report)

        return Command(goto="editor", update={"iterations": iterations, "revision": revision})

    return reviewer_node


def build_implement_loop(
    *,
    coder_llm: BaseChatModel,
    workspace: Workspace,
    settings: Settings | None = None,
    checkpointer=None,
    with_regression: bool = True,
):
    """Compile the subgraph. ``with_regression`` off skips the test-writing entry for
    a change that is not a bug fix and has no failing behaviour to pin."""
    settings = settings or get_settings()

    graph = StateGraph(ForgeState)
    graph.add_node("editor", make_apply_editor_node(llm=coder_llm, workspace=workspace))
    graph.add_node("verify", make_verify_node(workspace=workspace, settings=settings))
    graph.add_node("reviewer", make_reviewer_stub_node(settings=settings))

    if with_regression:
        graph.add_node(
            "regression",
            make_regression_node(llm=coder_llm, workspace=workspace, settings=settings),
        )
        graph.add_edge(START, "regression")
        graph.add_edge("regression", "editor")
    else:
        graph.add_edge(START, "editor")

    graph.add_edge("editor", "verify")
    graph.add_edge("verify", "reviewer")  # reviewer then Command(goto=...) routes
    return graph.compile(checkpointer=checkpointer)
