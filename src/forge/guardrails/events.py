"""The guardrail event log — cahier §8.5, and a never-cut deliverable.

The line the cahier draws is between *"we have guardrails"* and *"here are the 47
guardrail events from this session"*. Only the second is checkable, so the log is not
instrumentation bolted onto the guardrails — it is the deliverable, and every check in
this package is written to emit through it.

Two consequences worth stating:

**Clean runs are logged too.** ``ALLOWED`` events are written as deliberately as
``BLOCKED`` ones. A log containing only refusals cannot distinguish "nothing was
wrong" from "nothing ran", which is exactly the question an examiner asks.

**Nothing here can raise into the caller.** A guardrail whose *logging* can fail open
is worse than no guardrail, and a check that crashes the run it was protecting will be
switched off by the first person it inconveniences. Writes are best-effort and
swallow their own errors; the security decision has already been made by the time we
get here, and losing a log row must never turn a block into a pass.

Storage is the checkpoint SQLite file (descope §1 moved ``guardrail_events`` there
when Postgres was dropped) on its own short-lived connection, in WAL mode so it does
not contend with the async checkpointer writing the same file.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from forge.config import Settings, get_settings
from forge.models import GuardrailAction, GuardrailEvent, GuardrailStage

_DETAIL_LIMIT = 500
"""Details are a description, never the payload. A log that quotes the attack back
verbatim is a second copy of the attack, sitting somewhere less guarded."""

_SCHEMA = """
CREATE TABLE IF NOT EXISTS guardrail_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT    NOT NULL DEFAULT '',
    stage       TEXT    NOT NULL,
    rule        TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    score       REAL    NOT NULL DEFAULT 0.0,
    detail      TEXT    NOT NULL DEFAULT '',
    target      TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_guardrail_events_session ON guardrail_events (session_id);
CREATE INDEX IF NOT EXISTS ix_guardrail_events_rule ON guardrail_events (rule);
"""

_lock = threading.Lock()


class GuardrailLog:
    """Append-only event log over one SQLite file."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._ready = False

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        if not self._ready:
            # WAL so appending here does not block the async checkpointer, which is
            # writing its own tables in this same file.
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(_SCHEMA)
            connection.commit()
            self._ready = True
        return connection

    def record(self, event: GuardrailEvent) -> GuardrailEvent:
        """Append one event. Never raises — see the module docstring."""
        event.created_at = event.created_at or datetime.now(UTC).isoformat()
        event.detail = event.detail[:_DETAIL_LIMIT]
        try:
            with _lock, self._connect() as connection:
                connection.execute(
                    "INSERT INTO guardrail_events "
                    "(session_id, stage, rule, action, score, detail, target, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event.session_id,
                        event.stage.value,
                        event.rule,
                        event.action.value,
                        event.score,
                        event.detail,
                        event.target,
                        event.created_at,
                    ),
                )
        except Exception:
            # Deliberately every exception, not just sqlite3.Error: `_connect` also
            # touches the filesystem, and an unwritable path raises OSError/ValueError
            # before SQLite is ever reached. Losing a row must not turn a block into a
            # pass — the decision was made before this call and the caller acts on the
            # returned event either way, so the only thing a narrower catch would buy
            # is the guardrail failing open on a bad log path.
            pass
        return event

    def emit(
        self,
        *,
        stage: GuardrailStage,
        rule: str,
        action: GuardrailAction,
        session_id: str = "",
        score: float = 1.0,
        detail: str = "",
        target: str = "",
    ) -> GuardrailEvent:
        """Build and record in one call — what the checks actually use."""
        return self.record(
            GuardrailEvent(
                session_id=session_id,
                stage=stage,
                rule=rule,
                action=action,
                score=score,
                detail=detail,
                target=target,
            )
        )

    def events(
        self,
        *,
        session_id: str | None = None,
        stage: GuardrailStage | None = None,
        action: GuardrailAction | None = None,
        limit: int = 200,
    ) -> list[GuardrailEvent]:
        """Newest first. Filters compose; this is what the API route serves."""
        clauses: list[str] = []
        params: list[object] = []
        if session_id:
            clauses.append("session_id = ?")
            params.append(session_id)
        if stage:
            clauses.append("stage = ?")
            params.append(stage.value)
        if action:
            clauses.append("action = ?")
            params.append(action.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        try:
            with _lock, self._connect() as connection:
                rows = connection.execute(
                    f"SELECT * FROM guardrail_events {where} ORDER BY id DESC LIMIT ?",
                    [*params, max(1, limit)],
                ).fetchall()
        except Exception:
            return []
        return [_row_to_event(row) for row in rows]

    def counts_by_rule(self, *, session_id: str | None = None) -> dict[str, int]:
        """``{"injection.override": 3, ...}`` — the shape the §8.5 claim is made from."""
        try:
            with _lock, self._connect() as connection:
                if session_id:
                    rows = connection.execute(
                        "SELECT rule, COUNT(*) AS n FROM guardrail_events "
                        "WHERE session_id = ? GROUP BY rule ORDER BY n DESC",
                        (session_id,),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT rule, COUNT(*) AS n FROM guardrail_events "
                        "GROUP BY rule ORDER BY n DESC"
                    ).fetchall()
        except Exception:
            return {}
        return {row["rule"]: row["n"] for row in rows}

    def count(self, *, session_id: str | None = None) -> int:
        return sum(self.counts_by_rule(session_id=session_id).values())


def _row_to_event(row: sqlite3.Row) -> GuardrailEvent:
    return GuardrailEvent(
        session_id=row["session_id"],
        stage=GuardrailStage(row["stage"]),
        rule=row["rule"],
        action=GuardrailAction(row["action"]),
        score=row["score"],
        detail=row["detail"],
        target=row["target"],
        created_at=row["created_at"],
    )


_default: dict[str, GuardrailLog] = {}


def get_log(settings: Settings | None = None) -> GuardrailLog:
    """The process-wide log, on the configured checkpoint database."""
    settings = settings or get_settings()
    key = str(settings.checkpoint_db)
    if key not in _default:
        _default[key] = GuardrailLog(settings.checkpoint_db)
    return _default[key]


def reset_log() -> None:
    """Drop the cached handles. For tests that move the database underneath."""
    _default.clear()


def summarise(events: Iterable[GuardrailEvent]) -> str:
    """``3 blocked, 2 redacted, 42 allowed`` — the one-line form for a CLI or trace."""
    tally: dict[GuardrailAction, int] = {}
    for event in events:
        tally[event.action] = tally.get(event.action, 0) + 1
    if not tally:
        return "no guardrail events"
    order = [
        GuardrailAction.BLOCKED,
        GuardrailAction.REDACTED,
        GuardrailAction.FLAGGED,
        GuardrailAction.ALLOWED,
    ]
    return ", ".join(f"{tally[a]} {a.value}" for a in order if a in tally)
