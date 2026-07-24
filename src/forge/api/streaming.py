"""SSE framing for LangGraph events — cahier §11.

``stream_mode=["updates", "messages"]`` gives two very different things on one
channel: ``updates`` is a node finishing and returning a state delta, ``messages`` is
a token arriving from a model. The UI wants both and wants to tell them apart, so
they are normalised into typed events with an explicit ``type`` rather than forwarded
raw — a client should not have to know LangGraph's tuple shapes to render a timeline.

Four event types go over the wire:

``node``       a graph node finished; carries a short human summary of what changed
``token``      a streamed model token
``interrupt``  the run paused at a §5.5 gate and is waiting for ``/approve``
``done``       terminal, carries the final state summary
``error``      terminal, carries a typed message and never a traceback

The last two are why this module exists rather than a bare generator: a stream that
ends without saying *why* is indistinguishable from a dropped connection, and a
client waiting on an approval it was never told about will wait forever.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from pydantic import BaseModel, Field


class StreamEvent(BaseModel):
    """One SSE frame. ``data`` is always a JSON object, never a bare string."""

    type: str
    data: dict[str, Any] = Field(default_factory=dict)

    def sse(self) -> dict[str, str]:
        """The shape sse-starlette's EventSourceResponse expects."""
        return {"event": self.type, "data": json.dumps(self.data, default=str)}


def _summarise(node: str, delta: dict) -> dict[str, Any]:
    """A node's state delta reduced to what a timeline should show.

    Deliberately lossy: a ContextPack or a PatchSet serialised in full would make
    every frame kilobytes of JSON the UI does not render. Counts and verdicts are
    what a person watching a run actually reads.
    """
    summary: dict[str, Any] = {"node": node}
    if not isinstance(delta, dict):
        return summary

    def _field(value, name):
        """A checkpoint can hand a payload back as a dict; read either shape."""
        if value is None:
            return None
        return value.get(name) if isinstance(value, dict) else getattr(value, name, None)

    pack = delta.get("pack")
    if pack is not None:
        summary["chunks"] = len(_field(pack, "chunks") or [])
    plan = delta.get("plan")
    if plan is not None:
        summary["plan_steps"] = len(_field(plan, "steps") or [])
    if "patch_ok" in delta:
        summary["patch_ok"] = delta["patch_ok"]
    report = delta.get("report")
    if report is not None:
        summary["tests"] = getattr(report, "headline", lambda: str(report))()
    review = delta.get("review")
    if review is not None:
        summary["verdict"] = getattr(getattr(review, "verdict", None), "value", None)
    if delta.get("halted"):
        summary["halted"] = delta["halted"]
    if "iterations" in delta:
        summary["iteration"] = delta["iterations"]
    return summary


async def graph_events(graph, payload, config) -> AsyncIterator[StreamEvent]:
    """Run a graph turn and yield typed events until it ends, pauses, or fails.

    Errors are caught and emitted as an ``error`` frame rather than propagating: an
    exception escaping here aborts the SSE response mid-stream, and the client sees a
    truncated body with no explanation. A typed terminal frame is strictly more useful.
    """
    try:
        async for mode, chunk in graph.astream(
            payload, config=config, stream_mode=["updates", "messages"]
        ):
            if mode == "updates":
                for node, delta in (chunk or {}).items():
                    if node == "__interrupt__":
                        continue
                    yield StreamEvent(type="node", data=_summarise(node, delta))
            elif mode == "messages":
                message, _meta = chunk if isinstance(chunk, tuple) else (chunk, {})
                text = getattr(message, "content", "")
                if text:
                    yield StreamEvent(type="token", data={"text": text})

        state = await graph.aget_state(config)
        if state.next and getattr(state, "tasks", None):
            pending = [t for t in state.tasks if getattr(t, "interrupts", None)]
            if pending:
                payload_value = pending[0].interrupts[0].value
                yield StreamEvent(
                    type="interrupt",
                    data={"awaiting": "approval", "payload": payload_value},
                )
                return
        yield StreamEvent(type="done", data={"next": list(state.next)})
    except Exception as error:  # noqa: BLE001 — a stream must end with a reason
        yield StreamEvent(type="error", data={"message": f"{type(error).__name__}: {error}"[:500]})
