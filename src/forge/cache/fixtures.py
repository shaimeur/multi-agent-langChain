"""Record/replay cache sitting in front of every external call.

Every LLM completion and every third-party API response is persisted to
``data/fixtures/`` as readable JSON. The demo then runs with
``CACHE_MODE=replay``, where a miss is a hard error and nothing touches the
network — so a spent quota, a dead API or bad conference Wi-Fi cannot break it.

This is FORGE's real demo-day insurance policy, and it covers strictly more
failure modes than the Ollama offline profile does: the local model protects
against network loss only, this protects against network loss, exhausted
free-tier quota, a provider outage, and model drift between rehearsal and
defense. See docs/descope-v1.md §6.

The fixtures are committed to the repository on purpose: a fresh clone with no
API keys at all must be able to reproduce the graded demo. Because they are
committed, responses are scrubbed of secrets on write and secrets never
participate in a cache key.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from forge.config import CacheMode, Settings, get_settings

T = TypeVar("T")

_SECRET_ENV_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")
_REDACTED = "***REDACTED***"
_MIN_SECRET_LEN = 8


class FixtureMiss(RuntimeError):
    """No recorded response exists and the current mode forbids calling out."""


def _known_secrets(settings: Settings) -> list[str]:
    """Secret values to strip before anything is written to disk.

    Combines the declared credential fields with a scan of the environment, so
    a key we never modelled explicitly still cannot leak into a committed
    fixture. Sorted longest-first so overlapping values redact cleanly.
    """
    values = set(settings.secret_values())
    values.update(
        value
        for name, value in os.environ.items()
        if value and name.upper().endswith(_SECRET_ENV_SUFFIXES)
    )
    return sorted(
        (v for v in values if v and len(v) >= _MIN_SECRET_LEN),
        key=len,
        reverse=True,
    )


def _scrub(obj: Any, secrets: list[str]) -> Any:
    """Recursively replace every occurrence of a secret with a marker."""
    if isinstance(obj, str):
        for secret in secrets:
            obj = obj.replace(secret, _REDACTED)
        return obj
    if isinstance(obj, dict):
        return {k: _scrub(v, secrets) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub(v, secrets) for v in obj]
    return obj


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def fixture_key(namespace: str, request: Any) -> str:
    """Stable short digest identifying one (namespace, request) pair."""
    return hashlib.sha256(_canonical([namespace, request]).encode()).hexdigest()[:16]


class FixtureCache:
    """Replay-or-record store keyed on the request payload."""

    def __init__(self, root: Path, mode: CacheMode, settings: Settings) -> None:
        self.root = root
        self.mode = mode
        self._settings = settings
        self.stats: dict[str, int] = {"hit": 0, "recorded": 0, "miss": 0}

    def path_for(self, namespace: str, key: str) -> Path:
        """``llm.planner`` -> ``<root>/llm/planner/<key>.json``."""
        return self.root.joinpath(*namespace.split(".")) / f"{key}.json"

    def read(self, namespace: str, request: Any) -> Any | None:
        path = self.path_for(namespace, fixture_key(namespace, request))
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))["response"]

    def write(self, namespace: str, request: Any, response: Any) -> None:
        secrets = _known_secrets(self._settings)
        key = fixture_key(namespace, request)
        path = self.path_for(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {
            "namespace": namespace,
            "key": key,
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "request": _scrub(request, secrets),
            "response": _scrub(response, secrets),
        }
        path.write_text(json.dumps(envelope, indent=2, ensure_ascii=False), encoding="utf-8")

    def call(self, namespace: str, request: Any, fn: Callable[[], T]) -> T:
        """Return the recorded response, or invoke ``fn`` and record it.

        ``request`` must be JSON-serialisable, fully describe the call, and
        contain no credentials — it is both the cache key and the audit trail
        written into the fixture.
        """
        if self.mode is CacheMode.REFRESH:
            response = fn()
            self.write(namespace, request, response)
            self.stats["recorded"] += 1
            return response

        recorded = self.read(namespace, request)
        if recorded is not None:
            self.stats["hit"] += 1
            return recorded

        if self.mode is CacheMode.REPLAY:
            self.stats["miss"] += 1
            raise FixtureMiss(
                f"No fixture for {namespace} at "
                f"{self.path_for(namespace, fixture_key(namespace, request))}.\n"
                f"Request: {_canonical(request)[:400]}\n"
                "CACHE_MODE=replay forbids network calls. Re-run with "
                "CACHE_MODE=auto to record this response, then commit the fixture."
            )

        response = fn()
        self.write(namespace, request, response)
        self.stats["recorded"] += 1
        return response


_cache: FixtureCache | None = None


def get_cache() -> FixtureCache:
    global _cache
    if _cache is None:
        settings = get_settings()
        _cache = FixtureCache(settings.fixtures_dir, settings.cache_mode, settings)
    return _cache


def reset_cache() -> None:
    """Drop the singleton so tests can rebuild it against new settings."""
    global _cache
    _cache = None
