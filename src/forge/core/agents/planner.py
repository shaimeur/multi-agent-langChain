"""PLANNER node — a citation-backed ChangePlan (cahier §4/A2).

The planner turns a change request plus the retrieved context into an ordered plan
whose every step cites the evidence it rests on. The groundedness rule is enforced
*in code*, not trusted to the model: a step that cites nothing, or cites a snippet
that was never retrieved, is dropped before any code is written. If the plan cannot
be grounded, the planner sends the turn back to the RETRIEVER for more context —
capped, so it cannot ping-pong (§5.1).

Snippets are numbered for the model, and it cites those numbers; ``plan_change``
remaps each number to the real ``chunk_id`` so the grounding check runs against the
pack. Asking a weak local model for a small integer is far more reliable than asking
it to echo a 16-hex id.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.types import Command

from forge.core.agents.base import get_budget, get_pack, latest_user_text
from forge.core.state import ForgeState
from forge.models import ChangePlan, ContextPack

_SYSTEM = (
    "You are the PLANNER for FORGE. Given a change request and numbered code snippets, "
    "produce an ordered ChangePlan: each step names the single file it edits (target_path), "
    "says what to change (intent), and lists in `evidence` the snippet NUMBER(S) that justify "
    'it — put the number as a string in each evidence item\'s chunk_id (e.g. "2"). Cite only '
    "snippets you were given. If the snippets are not enough to plan the change, set "
    "needs_more_context=true and describe what is missing. Do not write code; only plan."
)


def _messages(request: str, pack: ContextPack) -> list:
    snippets = "\n\n".join(
        f"[{i}] {c.citation}" + (f"  {c.symbol}" if c.symbol else "") + f"\n{c.raw}"
        for i, c in enumerate(pack.chunks, start=1)
    )
    human = f"<context>\n{snippets}\n</context>\n\nChange request: {request}"
    return [SystemMessage(_SYSTEM), HumanMessage(human)]


def _remap_evidence(plan: ChangePlan, pack: ContextPack) -> ChangePlan:
    """Turn cited snippet numbers into real chunk_ids so grounding resolves."""
    for step in plan.steps:
        for ref in step.evidence:
            if ref.chunk_id.strip().isdigit():
                index = int(ref.chunk_id)
                if 1 <= index <= len(pack.chunks):
                    ref.chunk_id = pack.chunks[index - 1].chunk_id
    return plan


def plan_change(llm: BaseChatModel, request: str, pack: ContextPack) -> ChangePlan:
    """Ask for a ChangePlan and remap its evidence to chunk_ids. The caller enforces
    the grounding rule via ``ChangePlan.ungrounded_steps``."""
    planner = llm.with_structured_output(ChangePlan)
    plan = planner.invoke(_messages(request, pack))
    if not isinstance(plan, ChangePlan):
        return ChangePlan(needs_more_context=True, missing=request)
    return _remap_evidence(plan, pack)


def make_planner_node(
    *, llm: BaseChatModel, max_reentries: int = 1
) -> Callable[[ForgeState], Command]:
    def planner_node(state: ForgeState) -> Command:
        pack = get_pack(state) or ContextPack()
        budget = get_budget(state).spend(calls=1)
        reentries = state.get("plan_reentries", 0)

        try:
            plan = plan_change(llm, latest_user_text(state), pack)
        except Exception:
            plan = ChangePlan(needs_more_context=True, missing=latest_user_text(state))

        wants_more = plan.needs_more_context or not plan.steps or plan.ungrounded_steps(pack)
        if wants_more and reentries < max_reentries:
            return Command(
                goto="retriever",
                update={"plan": plan, "plan_reentries": reentries + 1, "budget": budget},
            )

        # Cap reached or already grounded: keep only the steps whose evidence resolves.
        grounded = plan.model_copy(update={"steps": [s for s in plan.steps if s.is_grounded(pack)]})
        goto = "editor" if grounded.steps else END
        return Command(goto=goto, update={"plan": grounded, "budget": budget})

    return planner_node
