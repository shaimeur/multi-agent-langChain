"""The repair loop and the full change graph — cahier §4, §5.1, §5.5.

The loop (D8), and around it the two human gates and the real reviewer (D9):

    START → planner → ╔plan_approval╗ → regression → editor → ╔patch_approval╗
                                                        ↑                  ↓
                      END ← reviewer ← verify ←────── apply ←──────────────┘
                             │  │
              escalate ←─────┘  └──→ editor   (REVISE, capped)

``editor`` builds a ``PatchSet`` and dry-runs ``git apply --check``; it **never
writes**. ``apply`` is the only node that touches disk, and the approval gate sits
between the two — so rejecting a patch requires no rollback, because nothing has
happened yet. That ordering is the whole reason the gate is worth having.

Three ways a run can stop other than success, none of them a traceback (cahier §9):
a human rejection, the iteration cap, and budget exhaustion. Each sets ``halted``
with a sentence a person can read. A fourth, loop pathology — the same file contested
three times — escalates to the human instead of quietly ending, because "we are stuck"
and "we ran out of turns" deserve different answers.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command

from forge.config import Settings, get_settings
from forge.core.agents.base import get_budget, get_pack, get_plan
from forge.core.agents.editor import edit_step
from forge.core.agents.planner import make_planner_node
from forge.core.agents.reviewer import make_reviewer
from forge.core.agents.tester import make_regression_node, make_verify_node
from forge.core.approval import (
    make_escalation_node,
    make_patch_approval_node,
    make_plan_approval_node,
)
from forge.core.state import ForgeState
from forge.core.workspace import Workspace
from forge.models import (
    ChangePlan,
    ContextPack,
    ExecutionReport,
    PatchSet,
    ReviewVerdict,
    RevisionRequest,
)
from forge.tools.patch import apply_patch_dryrun, apply_patchset


def _get_report(state: ForgeState) -> ExecutionReport | None:
    """A checkpoint can hand a typed value back as a dict — normalise either form."""
    report = state.get("report")
    if report is None or isinstance(report, ExecutionReport):
        return report
    return ExecutionReport(**report) if isinstance(report, dict) else None


def _get_revision(state: ForgeState) -> RevisionRequest | None:
    revision = state.get("revision")
    if revision is None or isinstance(revision, RevisionRequest):
        return revision
    return RevisionRequest(**revision) if isinstance(revision, dict) else None


def _get_patchset(state: ForgeState) -> PatchSet:
    patchset = state.get("patchset")
    if isinstance(patchset, PatchSet):
        return patchset
    return PatchSet(**patchset) if isinstance(patchset, dict) else PatchSet()


# --- nodes ----------------------------------------------------------------


def make_editor_node(*, llm: BaseChatModel, workspace: Workspace) -> Callable[[ForgeState], dict]:
    """Build the patch and dry-run it. Writes nothing — that is ``apply``'s job."""

    def editor_node(state: ForgeState) -> dict:
        plan = get_plan(state) or ChangePlan()
        revision = _get_revision(state)
        budget = get_budget(state)
        if not plan.steps:
            return {"patch_ok": False, "budget": budget}

        index = min(revision.target_step if revision else 0, len(plan.steps) - 1)
        patchset = edit_step(llm, plan.steps[index], workspace, revision)
        result = apply_patch_dryrun(workspace, patchset)

        return {
            "patchset": PatchSet(summary=plan.summary, patches=patchset.patches),
            "patch_ok": result.ok,
            "budget": budget.spend(calls=1),
            # Consumed: a stale revision would have the next pass fixing a failure
            # that no longer exists.
            "revision": None,
        }

    return editor_node


def make_apply_node(*, workspace: Workspace) -> Callable[[ForgeState], dict]:
    """The only node that writes. Re-checks rather than trusting the earlier dry run —
    the human sat in between, and the worktree may not be what it was."""

    def apply_node(state: ForgeState) -> dict:
        if not state.get("patch_ok"):
            return {}
        result = apply_patchset(workspace, _get_patchset(state))
        return {"patch_ok": result.ok}

    return apply_node


def make_reviewer_node(
    *,
    llm: BaseChatModel | None = None,
    settings: Settings | None = None,
    escalate_after: int | None = None,
) -> Callable[[ForgeState], Command]:
    """REVIEWER + routing. Approve ends the run; revise goes back to the editor.

    ``llm=None`` runs the three programmatic points only and records the other two as
    unevaluated — the honest posture when no reviewer model is configured, rather than
    a silent pass.
    """
    settings = settings or get_settings()
    escalate_after = escalate_after or settings.max_iterations_per_step
    reviewer = make_reviewer(llm=llm, settings=settings)

    def reviewer_node(state: ForgeState) -> Command:
        plan = get_plan(state) or ChangePlan()
        pack = get_pack(state) or ContextPack()
        report = _get_report(state)
        patchset = _get_patchset(state)
        iterations = state.get("iterations", 0) + 1
        budget = get_budget(state)

        revision_in = _get_revision(state)
        step_index = revision_in.target_step if revision_in else 0
        verdict: ReviewVerdict = reviewer(
            plan,
            pack,
            patchset,
            report,
            step_index=step_index,
            patch_applied=bool(state.get("patch_ok")),
        )
        update: dict = {"iterations": iterations, "review": verdict}

        if verdict.approved:
            return Command(goto=END, update={**update, "revision": None})

        # Which file the disagreement is about, for the pathology counter.
        contested = dict(state.get("contested", {}))
        path = plan.steps[step_index].target_path if step_index < len(plan.steps) else "?"
        contested[path] = contested.get(path, 0) + 1
        update["contested"] = contested

        exhausted = budget.exceeded(settings)
        if exhausted:
            return Command(goto=END, update={**update, "halted": exhausted})
        if contested[path] >= escalate_after:
            return Command(goto="escalate", update=update)
        if iterations >= settings.max_iterations_per_step:
            return Command(
                goto=END,
                update={
                    **update,
                    "halted": f"stopped after {iterations} attempts: "
                    + "; ".join(verdict.feedback[:2]),
                },
            )
        return Command(goto="editor", update={**update, "revision": verdict.as_revision(report)})

    return reviewer_node


# --- graphs ---------------------------------------------------------------


def _add_loop_nodes(
    graph: StateGraph,
    *,
    coder_llm: BaseChatModel,
    reviewer_llm: BaseChatModel | None,
    workspace: Workspace,
    settings: Settings,
    with_regression: bool,
    patch_approval: bool,
) -> str:
    """The loop body, shared by both graphs. Returns the node it should be entered at."""
    graph.add_node("editor", make_editor_node(llm=coder_llm, workspace=workspace))
    graph.add_node("apply", make_apply_node(workspace=workspace))
    graph.add_node("verify", make_verify_node(workspace=workspace, settings=settings))
    graph.add_node("reviewer", make_reviewer_node(llm=reviewer_llm, settings=settings))
    graph.add_node("escalate", make_escalation_node())
    graph.add_node(
        "patch_approval",
        make_patch_approval_node(workspace=workspace, next_node="apply", enabled=patch_approval),
    )

    graph.add_edge("editor", "patch_approval")  # the gate sits before the write
    graph.add_edge("apply", "verify")
    graph.add_edge("verify", "reviewer")  # reviewer then Command(goto=...) routes

    if with_regression:
        graph.add_node(
            "regression",
            make_regression_node(llm=coder_llm, workspace=workspace, settings=settings),
        )
        graph.add_edge("regression", "editor")
        return "regression"
    return "editor"


def build_implement_loop(
    *,
    coder_llm: BaseChatModel,
    workspace: Workspace,
    reviewer_llm: BaseChatModel | None = None,
    settings: Settings | None = None,
    checkpointer=None,
    with_regression: bool = True,
    patch_approval: bool = False,
):
    """The subgraph alone, without the planner or the plan gate.

    ``patch_approval`` defaults off here: this is the "run the loop" entry point used
    by the benchmark and the tests, where there is no human to ask. The full graph
    turns it on.
    """
    settings = settings or get_settings()
    graph = StateGraph(ForgeState)
    entry = _add_loop_nodes(
        graph,
        coder_llm=coder_llm,
        reviewer_llm=reviewer_llm,
        workspace=workspace,
        settings=settings,
        with_regression=with_regression,
        patch_approval=patch_approval,
    )
    graph.add_edge(START, entry)
    return graph.compile(checkpointer=checkpointer)


def build_change_graph(
    *,
    planner_llm: BaseChatModel,
    coder_llm: BaseChatModel,
    workspace: Workspace,
    reviewer_llm: BaseChatModel | None = None,
    settings: Settings | None = None,
    checkpointer=None,
    with_regression: bool = True,
    approvals: bool = True,
    retriever_node: Callable | None = None,
):
    """The whole change path, planner included, with both §5.5 control points.

    The planner is built here rather than passed in, so its success exit is wired to
    ``plan_approval`` by construction. A caller that supplied its own planner node
    could hand over one that goes straight to the editor, and the plan gate would be
    silently bypassed — the one thing this graph exists to prevent.

    Needs a checkpointer to be useful: ``interrupt()`` persists the state and the run
    resumes from it, so without one a paused session has nowhere to wait.
    """
    settings = settings or get_settings()
    graph = StateGraph(ForgeState)

    entry = _add_loop_nodes(
        graph,
        coder_llm=coder_llm,
        reviewer_llm=reviewer_llm,
        workspace=workspace,
        settings=settings,
        with_regression=with_regression,
        patch_approval=approvals,
    )
    graph.add_node("plan_approval", make_plan_approval_node(next_node=entry, enabled=approvals))

    if retriever_node is not None:
        graph.add_node("retriever", retriever_node)
        graph.add_edge("retriever", "planner")
    graph.add_node(
        "planner",
        make_planner_node(
            llm=planner_llm,
            success_node="plan_approval",
            # With no retriever wired, needs_more_context has nowhere to go but out.
            # Ending is the honest answer: D12 wires the retriever into this graph.
            retry_node="retriever" if retriever_node is not None else END,
            max_reentries=1 if retriever_node is not None else 0,
        ),
    )

    graph.add_edge(START, "planner")
    return graph.compile(checkpointer=checkpointer)
