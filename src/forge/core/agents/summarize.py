"""Sliding-summary node — bound conversational memory (cahier §7).

Past a message threshold, fold the oldest turns into a running summary and drop them
from the live message list with ``RemoveMessage`` (which the ``add_messages`` reducer
honours), keeping only the last few turns verbatim. Deterministic by default — no LLM
call — so it works on the offline profile and in tests; an LLM summariser is a later
refinement. For a short session it is a no-op; it earns its place on long ones.
"""

from __future__ import annotations

from collections.abc import Callable

from langchain_core.messages import HumanMessage, RemoveMessage

from forge.core.agents.base import message_text
from forge.core.state import ForgeState


def make_summary_node(
    *, keep_last: int = 6, trigger_messages: int = 12
) -> Callable[[ForgeState], dict]:
    def summary_node(state: ForgeState) -> dict:
        messages = state.get("messages", [])
        if len(messages) <= max(trigger_messages, keep_last):
            return {}

        old = messages[:-keep_last]
        prior = state.get("summary", "")
        questions = [
            t for m in old if isinstance(m, HumanMessage) and (t := message_text(m).strip())
        ]

        folded = prior
        if questions:
            joined = "; ".join(questions)
            folded = (prior + "\n" if prior else "") + f"Earlier the user asked: {joined}."

        removes = [RemoveMessage(id=m.id) for m in old if getattr(m, "id", None) is not None]
        return {"summary": folded, "messages": removes}

    return summary_node
