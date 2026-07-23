"""The graph's shared state, its reducers, and the budget guard — cahier §4, §5.4.

``ForgeState`` is what every node reads and writes; LangGraph merges each node's
returned partial into it through the reducers declared here. Messages accumulate
(``add_messages``), retrieved chunks accumulate *deduplicated* (``merge_chunks``),
and everything else is last-write-wins. The whole state is what the checkpointer
persists per ``thread_id`` — which is what lets a session survive a process
restart (cahier §7, the C4 proof).

Typed payloads, not loose dicts, cross every node boundary — the point §5.4 makes
about what carries information between agents.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from pydantic import BaseModel

from forge.config import Settings
from forge.models import Chunk, ContextPack, GroundedAnswer, RouteDecision


def _as_chunk(chunk: Chunk | dict) -> Chunk:
    """A checkpoint may round-trip a Chunk back as a dict; normalise either form."""
    return chunk if isinstance(chunk, Chunk) else Chunk(**chunk)


def merge_chunks(existing: list[Chunk] | None, new: list[Chunk] | None) -> list[Chunk]:
    """Accumulate retrieved chunks across RETRIEVER re-entries, deduped by id.

    The retriever returns only the chunks it has *not* surfaced before — the
    differential pack of cahier §4/A1 — so this reducer is what grows the full
    working set monotonically without re-adding a chunk a later query also hit.
    First-seen order is preserved so the earliest, best-ranked evidence leads.
    Values are normalised to ``Chunk`` because a resumed session's ``existing``
    may have come back from the checkpoint as plain dicts.
    """
    merged = [_as_chunk(c) for c in (existing or [])]
    seen = {c.chunk_id for c in merged}
    for chunk in new or []:
        chunk = _as_chunk(chunk)
        if chunk.chunk_id not in seen:
            seen.add(chunk.chunk_id)
            merged.append(chunk)
    return merged


class Budget(BaseModel):
    """The A0 budget guard (cahier §4). Cumulative over a session's turns.

    Every LLM node reports what it spent; the supervisor refuses to continue once a
    cap is hit and the run returns a graceful partial answer rather than a stack
    trace (cahier §9, finished on D9). Only serializable counters live here —
    wall-clock is enforced per graph invocation by the runner, not persisted, so a
    session resumed tomorrow is not instantly budget-killed.
    """

    llm_calls: int = 0
    tokens: int = 0

    def spend(self, *, calls: int = 1, tokens: int = 0) -> Budget:
        """A new Budget with the spend added — never mutated in place, so the
        reducer sees a fresh value and the checkpoint is a clean snapshot."""
        return Budget(llm_calls=self.llm_calls + calls, tokens=self.tokens + tokens)

    def exceeded(self, settings: Settings) -> str | None:
        """The reason a run must stop, or None while it may continue."""
        if self.llm_calls >= settings.max_llm_calls_per_run:
            return f"LLM-call cap reached ({settings.max_llm_calls_per_run})"
        if self.tokens >= settings.max_tokens_per_run:
            return f"token cap reached ({settings.max_tokens_per_run})"
        return None


class ForgeState(TypedDict, total=False):
    """What flows through the graph. ``total=False`` so a node returns only the
    keys it changed and the reducers merge them in."""

    messages: Annotated[list[AnyMessage], add_messages]
    """The conversation, oldest-first. Folded turns are replaced by ``summary``."""
    session_id: str
    """The checkpoint thread id — the same session resumes under it after a restart."""
    chunks: Annotated[list[Chunk], merge_chunks]
    """Every chunk retrieved this session, deduplicated across retriever re-entries."""
    pack: ContextPack | None
    """The current packed context the answerer reads (parent-expanded, budget-bounded)."""
    route: RouteDecision | None
    """The supervisor's last routing verdict, kept for the trace."""
    budget: Budget
    """The cumulative A0 guard."""
    summary: str
    """The sliding summary of folded-away older turns (cahier §7)."""
    answer: GroundedAnswer | None
    """The latest grounded answer, for the CLI / API to render."""
