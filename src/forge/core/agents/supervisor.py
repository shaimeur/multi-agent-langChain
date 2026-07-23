"""SUPERVISOR node — routing and budget only, never content (cahier §4).

The supervisor is a *router*: it emits a small typed ``RouteDecision`` and a
``Command(goto=...)``, never prose. It runs on the cheap ROUTER tier and guards the
A0 budget — once a cap is hit it stops gracefully, answering from whatever context
exists rather than looping into a stack trace (cahier §9).

Structured output can misfire on a weak local model, so routing has a deterministic
safety net: an unparseable verdict falls back to RETRIEVE, which is the right action
for almost every real question. The gate that keeps a bad model from breaking the
graph, not a place that trusts it.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END
from langgraph.types import Command

from forge.config import Settings
from forge.core.agents.base import get_budget, get_pack, latest_user_text
from forge.core.state import ForgeState
from forge.models import Route, RouteDecision

_ROUTES = {Route.RETRIEVE: "retriever", Route.ANSWER: "answer", Route.END: END}

_SYSTEM = (
    "You are the router for FORGE, a code question-answering assistant. Classify the "
    "user's latest message and reply with a single route:\n"
    "- retrieve: answering needs code from the repository (almost every question).\n"
    "- answer: the needed code is already in context; this is a follow-up about it.\n"
    "- end: a greeting, thanks, or anything that needs no code lookup.\n"
    "Do not answer the question. Only route it."
)


def make_supervisor_node(
    *, llm: BaseChatModel, settings: Settings
) -> Callable[[ForgeState], Command]:
    router = llm.with_structured_output(RouteDecision)

    def supervisor_node(state: ForgeState) -> Command:
        budget = get_budget(state)

        reason = budget.exceeded(settings)
        if reason:
            pack = get_pack(state)
            grounded_context = bool(pack and pack.chunks)
            route = Route.ANSWER if grounded_context else Route.END
            return Command(
                goto=_ROUTES[route],
                update={"route": RouteDecision(route=route, rationale=f"budget: {reason}")},
            )

        decision = _decide(router, state)
        return Command(
            goto=_ROUTES[decision.route],
            update={"route": decision, "budget": budget.spend(calls=1)},
        )

    return supervisor_node


def _decide(router, state: ForgeState) -> RouteDecision:
    """Structured routing with a safe default (see module docstring)."""
    question = latest_user_text(state)
    if not question:
        return RouteDecision(route=Route.END, rationale="no user message")
    try:
        decision = router.invoke([SystemMessage(_SYSTEM), HumanMessage(question)])
        if isinstance(decision, RouteDecision):
            return decision
    except Exception:
        pass
    return RouteDecision(route=Route.RETRIEVE, rationale="fallback")
