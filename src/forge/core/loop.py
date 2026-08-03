"""The repair loop and the full change graph — cahier §4, §5.1, §5.5.

The loop (D8), and around it the two human gates and the real reviewer (D9):

    START → retriever → planner → ╔plan_approval╗ → regression → editor → ╔patch_approval╗
               ↑           │                                         ↑                  ↓
               └───────────┘        END ← reviewer ← verify ←────── apply ←─────────────┘
            (needs_more_context)           │  │
                                escalate ←─┘  └──→ editor   (REVISE, capped)

The retriever is optional — a caller that already holds a ``ContextPack`` (the tests,
and any graph that retrieved earlier in the turn) omits it and the graph starts at the
planner. Every caller that starts from a bare request must pass one, or the planner
opens on an empty pack and the run dead-ends with nothing to cite.

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

from forge.config import Settings, get_settings
from forge.core.agents.base import get_patchset
from forge.core.agents.editor import make_editor_node
from forge.core.agents.planner import make_planner_node
from forge.core.agents.reviewer import make_reviewer_node
from forge.core.agents.tester import make_regression_node, make_verify_node
from forge.core.approval import (
    make_escalation_node,
    make_patch_approval_node,
    make_plan_approval_node,
)
from forge.core.state import ForgeState
from forge.core.workspace import Workspace
from forge.tools.patch import apply_patchset


def make_apply_node(*, workspace: Workspace) -> Callable[[ForgeState], dict]:
    """The only node that writes to disk.

    Kept here rather than with the EDITOR on purpose: the editor is the agent that
    *proposes* a change and D6's rule is that it never writes, so the write is a
    separate deterministic step the graph schedules after the human gate.

    Re-checks rather than trusting the earlier dry run — a human sat in between, and
    the worktree may not be what it was when the patch was built.
    """

    def apply_node(state: ForgeState) -> dict:
        if not state.get("patch_ok"):
            return {}
        result = apply_patchset(workspace, get_patchset(state))
        return {"patch_ok": result.ok}

    return apply_node


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
            # Ending is the honest answer — the caller gave the planner no way to look
            # anything up. Callers that want a plan pass ``retriever_node``.
            retry_node="retriever" if retriever_node is not None else END,
            max_reentries=1 if retriever_node is not None else 0,
        ),
    )

    # Retrieve *before* planning when we can. Entering at the planner would spend a
    # model call on an empty pack, which returns needs_more_context by construction —
    # the retry edge would then reach the retriever, but a whole call later and with
    # the re-entry budget already half spent. The planner's own retry loop is for
    # context that turned out to be insufficient, not for the first look.
    graph.add_edge(START, "retriever" if retriever_node is not None else "planner")
    return graph.compile(checkpointer=checkpointer)
