"""Sessions and their counters — the state behind the §11 routes.

A FORGE session owns two things: a **git worktree** (created on ``POST /v1/sessions``,
removed on close) and a **checkpointer thread** keyed by the same id. The split
matters — the worktree is this process's, while the thread is durable and outlives a
restart, which is what lets D9's ``interrupt()`` sit paused for hours and resume.

The registry is in-process, deliberately and with the same caveat as the rate limiter:
Redis was cut (cahier §14 cut list, item 1), so N workers means N registries, and a
restart loses the worktrees but *not* the conversations. Stated here rather than
discovered later.

Metrics are per session and cumulative, because that is the question anyone actually
asks — "what did this run cost" rather than "what has the process totalled since
boot". ``/v1/metrics`` serves both.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from forge.config import Settings, get_settings
from forge.core.workspace import Workspace, create_workspace, remove_workspace


class SessionMetrics(BaseModel):
    """Cost, latency and token counters for one session (cahier §11 `/v1/metrics`)."""

    turns: int = 0
    llm_calls: int = 0
    tokens: int = 0
    guardrail_events: int = 0
    latency_ms_total: float = 0.0
    latency_ms_last: float = 0.0
    errors: int = 0

    @property
    def latency_ms_mean(self) -> float:
        return self.latency_ms_total / self.turns if self.turns else 0.0

    def record_turn(self, *, duration_ms: float, llm_calls: int = 0, tokens: int = 0) -> None:
        self.turns += 1
        self.latency_ms_last = duration_ms
        self.latency_ms_total += duration_ms
        self.llm_calls += llm_calls
        self.tokens += tokens


class SessionInfo(BaseModel):
    """What ``POST /v1/sessions`` returns and ``GET /v1/metrics`` reports."""

    session_id: str
    created_at: str
    workspace: str
    branch: str
    metrics: SessionMetrics = Field(default_factory=SessionMetrics)


@dataclass
class Session:
    """A live session: its worktree, its counters, its clock."""

    session_id: str
    workspace: Workspace
    created_at: str
    metrics: SessionMetrics = field(default_factory=SessionMetrics)

    def info(self) -> SessionInfo:
        return SessionInfo(
            session_id=self.session_id,
            created_at=self.created_at,
            workspace=str(self.workspace.path),
            branch=self.workspace.branch,
            metrics=self.metrics,
        )


class SessionStore:
    """The in-process registry. One worktree per session, torn down on close."""

    def __init__(self, settings: Settings | None = None):
        self._settings = settings
        self._sessions: dict[str, Session] = {}

    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    def create(self, session_id: str | None = None) -> Session:
        session_id = session_id or uuid.uuid4().hex[:12]
        if session_id in self._sessions:
            return self._sessions[session_id]
        workspace = create_workspace(session_id, settings=self.settings)
        session = Session(
            session_id=session_id,
            workspace=workspace,
            created_at=datetime.now(UTC).isoformat(),
        )
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def list(self) -> list[Session]:
        return list(self._sessions.values())

    def close(self, session_id: str) -> bool:
        """Remove the worktree. The *conversation* survives in the checkpointer —
        closing a session frees disk, it does not erase history."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        remove_workspace(session.workspace)
        return True

    def close_all(self) -> None:
        for session_id in list(self._sessions):
            self.close(session_id)


_store: SessionStore | None = None


def get_store(settings: Settings | None = None) -> SessionStore:
    global _store
    if _store is None:
        _store = SessionStore(settings)
    return _store


def reset_store() -> None:
    """Tear every session down. For tests, and for a clean process exit."""
    global _store
    if _store is not None:
        _store.close_all()
    _store = None


class Timer:
    """Wall-clock for one turn, in milliseconds."""

    def __enter__(self) -> Timer:
        self._start = time.monotonic()
        self.ms = 0.0
        return self

    def __exit__(self, *exc) -> None:
        self.ms = (time.monotonic() - self._start) * 1000
