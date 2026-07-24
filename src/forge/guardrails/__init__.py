"""Guardrails — three deterministic layers around the graph (cahier §8).

``sentinel_in`` validates the request, ``injection`` defends the retrieved-content
attack surface, ``policy`` is the pre-LLM tool and filesystem whitelist, and
``sentinel_out`` vets what leaves. Every one of them emits through ``events``, which
is the deliverable §8.5 actually asks for: not "we have guardrails" but "here are the
47 guardrail events from this session".

Security-sensitive (CLAUDE.md): do not relax a path allowlist, a command whitelist or
a resource cap here without flagging it first.
"""

from __future__ import annotations

from forge.guardrails.events import GuardrailLog, get_log, reset_log, summarise
from forge.guardrails.injection import (
    SPOTLIGHT_DIRECTIVE,
    classify,
    scan_chunks,
    spotlight,
    strip_instructions,
)
from forge.guardrails.policy import ALLOWED_COMMANDS, check_command, check_path
from forge.guardrails.sentinel_in import check_input, get_rate_limiter, redact_secrets
from forge.guardrails.sentinel_out import check_answer, check_patchset

__all__ = [
    "ALLOWED_COMMANDS",
    "SPOTLIGHT_DIRECTIVE",
    "GuardrailLog",
    "check_answer",
    "check_command",
    "check_input",
    "check_patchset",
    "check_path",
    "classify",
    "get_log",
    "get_rate_limiter",
    "redact_secrets",
    "reset_log",
    "scan_chunks",
    "spotlight",
    "strip_instructions",
    "summarise",
]
