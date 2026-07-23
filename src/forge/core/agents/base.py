"""Helpers shared by the agent nodes — message reading and post-checkpoint coercion.

A checkpoint round-trips the state through a serializer, and a typed value can come
back either as its pydantic model or as a plain dict depending on the serde. Every
node reads ``Budget`` / ``ContextPack`` through the coercers here so it never has to
care which it got — a small tax that makes the restart path robust.
"""

from __future__ import annotations

from langchain_core.messages import AnyMessage, HumanMessage

from forge.core.state import Budget, ForgeState
from forge.models import ContextPack


def message_text(message: AnyMessage) -> str:
    """A message's text as a plain string, whatever shape its content takes."""
    content = getattr(message, "content", "")
    return content if isinstance(content, str) else str(content)


def latest_user_text(state: ForgeState) -> str:
    """The most recent human turn's text — the question a node is working on."""
    for message in reversed(state.get("messages", [])):
        if isinstance(message, HumanMessage):
            return message_text(message).strip()
    return ""


def get_budget(state: ForgeState) -> Budget:
    budget = state.get("budget")
    if isinstance(budget, Budget):
        return budget
    if isinstance(budget, dict):
        return Budget(**budget)
    return Budget()


def get_pack(state: ForgeState) -> ContextPack | None:
    pack = state.get("pack")
    if pack is None or isinstance(pack, ContextPack):
        return pack
    if isinstance(pack, dict):
        return ContextPack(**pack)
    return None


def est_tokens(text: str) -> int:
    """The same rough char→token heuristic the packer uses, for the budget guard."""
    return max(len(text) // 4, 1)
