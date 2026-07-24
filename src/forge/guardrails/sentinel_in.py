"""``sentinel_in`` — input validation before anything else runs (cahier §8.1).

A deterministic node in front of the SUPERVISOR, not an agent (§4/S). It checks four
things, logs every one of them, and either passes the turn through or refuses it:

* **shape and size** — a prompt over the cap is refused rather than truncated, since
  truncating an attack leaves an attack;
* **rate** — per-session, in-process. Redis was cut on the cahier's own cut list, so
  this is a token bucket in memory; it therefore does not survive a restart and does
  not span workers, which is written down rather than implied;
* **secrets leaving the user** — a key pasted into a prompt gets redacted before it
  can reach a provider, a fixture, or the event log;
* **direct injection** — the tier-1 heuristics, shared with the indirect path.

Refusals are events, not exceptions. The caller gets a ``SentinelDecision`` and
decides how to answer; a guardrail that raises into the request handler produces a
500 where it should produce a polite "no".
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

from forge.config import Settings, get_settings
from forge.guardrails.events import GuardrailLog, get_log
from forge.guardrails.injection import classify
from forge.models import GuardrailAction, GuardrailEvent, GuardrailStage

MAX_PROMPT_CHARS = 20_000
"""Well above any real question and far below a context-stuffing attempt."""

RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_S = 60.0

# Credential shapes worth redacting out of user input. Deliberately the high-confidence
# ones: a false positive here mangles a legitimate prompt, which is its own failure.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("google", re.compile(r"AIza[\w-]{30,}")),
    ("openai", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("groq", re.compile(r"gsk_[A-Za-z0-9]{20,}")),
    ("github", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("aws", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)

_REDACTION = "[redacted-secret]"


@dataclass
class SentinelDecision:
    """Whether the turn may proceed, with the (possibly rewritten) text."""

    allowed: bool
    text: str = ""
    events: list[GuardrailEvent] = field(default_factory=list)
    reason: str = ""

    def __bool__(self) -> bool:
        return self.allowed


class RateLimiter:
    """A per-session sliding window, in process.

    In-process because Redis was cut (cahier §14 cut list, item 1). The honest
    consequences: it resets on restart and is per-worker, so N workers permit N times
    the limit. Adequate for a single-process demo, and stated rather than discovered.
    """

    def __init__(self, limit: int = RATE_LIMIT_REQUESTS, window_s: float = RATE_LIMIT_WINDOW_S):
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, list[float]] = {}

    def check(self, session_id: str, *, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        recent = [t for t in self._hits.get(session_id, []) if now - t < self.window_s]
        recent.append(now)
        self._hits[session_id] = recent
        return len(recent) <= self.limit

    def reset(self) -> None:
        self._hits.clear()


_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    return _limiter


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """Mask any credential shape in ``text``. Returns the text and the kinds found."""
    found: list[str] = []
    cleaned = text
    for kind, pattern in _SECRET_PATTERNS:
        cleaned, hits = pattern.subn(_REDACTION, cleaned)
        if hits:
            found.append(kind)
    return cleaned, found


def check_input(
    text: str,
    *,
    session_id: str = "",
    settings: Settings | None = None,
    log: GuardrailLog | None = None,
    limiter: RateLimiter | None = None,
) -> SentinelDecision:
    """Run every input control, logging each. The turn proceeds unless refused."""
    settings = settings or get_settings()
    log = log or get_log()
    limiter = limiter or _limiter
    events: list[GuardrailEvent] = []

    def emit(rule, action, detail="", score=1.0):
        event = log.emit(
            stage=GuardrailStage.SENTINEL_IN,
            rule=rule,
            action=action,
            session_id=session_id,
            score=score,
            detail=detail,
        )
        events.append(event)
        return event

    if not text or not text.strip():
        emit("input.empty", GuardrailAction.BLOCKED, "an empty prompt")
        return SentinelDecision(False, "", events, "the request was empty")

    if len(text) > MAX_PROMPT_CHARS:
        emit(
            "input.too_long",
            GuardrailAction.BLOCKED,
            f"{len(text)} chars over the {MAX_PROMPT_CHARS} cap",
        )
        return SentinelDecision(False, "", events, "the request is too long")

    if not limiter.check(session_id or "anonymous"):
        emit("input.rate_limited", GuardrailAction.BLOCKED, f"over {limiter.limit}/min")
        return SentinelDecision(False, "", events, "too many requests — slow down")

    cleaned, secrets = redact_secrets(text)
    if secrets:
        # Redacted, not refused: the user probably pasted a log by accident, and the
        # useful response is to carry on without the credential rather than to stop.
        emit("input.secret", GuardrailAction.REDACTED, f"redacted: {', '.join(secrets)}")

    findings = classify(cleaned)
    for finding in findings:
        emit("input." + finding.rule.split(".", 1)[1], GuardrailAction.FLAGGED, finding.excerpt)

    if not secrets and not findings:
        emit("input.clean", GuardrailAction.ALLOWED)

    # Flagged, never blocked: a user is entitled to *ask about* prompt injection, and
    # a coding assistant that refuses to discuss the attack it defends against is
    # useless for the one job it has. The defence is spotlighting and privilege
    # invariance downstream, not refusing the word "ignore".
    return SentinelDecision(True, cleaned, events)
