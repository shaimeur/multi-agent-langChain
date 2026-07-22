"""Bridges LangChain's cache interface onto the fixture store.

Installing this means model completions are recorded and replayed by exactly
the same mechanism as third-party API responses, so the whole demo — agent
reasoning included — is reproducible offline from a fresh clone.
"""

from __future__ import annotations

from typing import Any

from langchain_core.caches import BaseCache
from langchain_core.load import dumpd, load

from forge.cache import FixtureMiss, get_cache
from forge.config import CacheMode

NAMESPACE = "llm"

# Fixtures are trusted — we wrote them — but deserialisation is still bounded to
# langchain_core's own serialisables rather than left on the permissive default.
# A project whose thesis is deterministic guardrails should not hold a loose
# `load()` on a file read off disk.
_ALLOWED_OBJECTS = "core"


class FixtureLLMCache(BaseCache):
    """Replay-or-record cache for chat completions.

    ``llm_string`` is LangChain's serialisation of the model and its
    parameters, so changing model or temperature correctly yields a new key.
    """

    def lookup(self, prompt: str, llm_string: str) -> Any | None:
        cache = get_cache()
        request = {"llm": llm_string, "prompt": prompt}
        recorded = cache.read(NAMESPACE, request)

        if recorded is not None:
            cache.stats["hit"] += 1
            return [load(g, allowed_objects=_ALLOWED_OBJECTS) for g in recorded]

        if cache.mode is CacheMode.REPLAY:
            # Returning None here would let LangChain silently call the API and
            # break the offline guarantee, so a miss has to be fatal instead.
            cache.stats["miss"] += 1
            raise FixtureMiss(
                f"No recorded completion for this prompt under {llm_string[:120]}.\n"
                "CACHE_MODE=replay forbids network calls. Re-run with "
                "CACHE_MODE=auto to record it, then commit the fixture."
            )
        return None

    def update(self, prompt: str, llm_string: str, return_val: Any) -> None:
        cache = get_cache()
        request = {"llm": llm_string, "prompt": prompt}
        cache.write(NAMESPACE, request, [dumpd(g) for g in return_val])
        cache.stats["recorded"] += 1

    def clear(self, **kwargs: Any) -> None:
        """Not implemented on purpose — fixtures are deleted deliberately, by hand."""
        raise NotImplementedError("Delete files under data/fixtures/llm/ to clear.")
