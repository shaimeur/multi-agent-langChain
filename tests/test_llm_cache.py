"""Model completions must replay offline exactly like API responses do."""

from __future__ import annotations

import pytest
from langchain_core.globals import set_llm_cache
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from forge.cache import FixtureCache, FixtureMiss
from forge.config import CacheMode
from forge.llm.cache import FixtureLLMCache


@pytest.fixture
def wire(settings, monkeypatch):
    """Install the fixture cache globally, backed by an isolated store."""

    def _wire(mode: CacheMode) -> FixtureCache:
        cache = FixtureCache(settings.fixtures_dir, mode, settings)
        monkeypatch.setattr("forge.llm.cache.get_cache", lambda: cache)
        set_llm_cache(FixtureLLMCache())
        return cache

    yield _wire
    set_llm_cache(None)


def _model(responses: list[str]) -> FakeListChatModel:
    return FakeListChatModel(responses=responses, cache=True)


def test_completion_replays_instead_of_advancing_the_model(wire):
    """A replayed run must return the recorded text, not the next response."""
    wire(CacheMode.AUTO)
    assert _model(["first", "second"]).invoke("plan the refactor").content == "first"

    wire(CacheMode.REPLAY)
    assert _model(["first", "second"]).invoke("plan the refactor").content == "first"


def test_unrecorded_prompt_is_fatal_in_replay_mode(wire):
    """Silently falling through to the network would void the offline guarantee."""
    cache = wire(CacheMode.REPLAY)

    with pytest.raises(FixtureMiss, match="CACHE_MODE=replay"):
        _model(["anything"]).invoke("a prompt nobody recorded")

    assert cache.stats["miss"] == 1


def test_recording_lands_under_the_llm_namespace(wire, settings):
    wire(CacheMode.AUTO)
    _model(["ok"]).invoke("locate the auth handler")

    assert len(list((settings.fixtures_dir / "llm").glob("*.json"))) == 1


# --- the shipped defaults must be able to reach the shipped fixtures --------


def _answer_fixture_models() -> set[str]:
    """Models the committed *grounded-answer* fixtures were recorded under.

    Keyed on the answer prompt itself rather than on the file list, because the
    question is not "does this name appear anywhere in the store" — the coder writes
    under a second id on purpose — but "can the reasoner reach the answers recorded
    for it".
    """
    import json
    import re

    from forge.config import PROJECT_ROOT
    from forge.rag.answer import _SYSTEM

    marker = _SYSTEM.split("{repo}")[0]
    models: set[str] = set()
    for path in (PROJECT_ROOT / "data" / "fixtures" / "llm").glob("*.json"):
        request = json.loads(path.read_text(encoding="utf-8")).get("request", {})
        if marker not in request.get("prompt", ""):
            continue
        # Read the id out of the serialised key rather than parsing it: providers
        # serialise the constructor differently (Ollama nests it), and this only has
        # to answer "which model", not "reconstruct the model".
        found = re.search(r'\\?"model(?:_name)?\\?"\s*:\s*\\?"([^"\\]+)', request.get("llm", ""))
        if found:
            models.add(found.group(1))
    return models


def test_the_shipped_reasoner_can_reach_the_recorded_answers():
    """A default the fixtures were not recorded under is a dead offline demo.

    The model id is part of the LangChain cache key, so renaming it invalidates every
    recorded completion at once — and the failure stays invisible until someone runs
    `CACHE_MODE=replay forge ask` on a clean checkout, where it raises FixtureMiss with
    the repository looking perfectly clean. That is exactly what shipping
    `gemini-3.5-flash` did while all 37 grounded-answer fixtures were recorded against
    `gemini-flash-latest`, and it survived both a green suite and the C9 clean-machine
    run, because neither of those drives a real completion.

    Not covered here, deliberately: the coder (its fixtures live under a second model
    id, which is the point — two ids are two quota pools) and the router (the
    supervisor has a deterministic fallback and records no routing completion).
    """
    from forge.config import Settings

    recorded = _answer_fixture_models()
    if not recorded:
        pytest.skip("no committed grounded-answer fixtures to check against")

    shipped = Settings(_env_file=None).gemini_reasoner_model

    assert shipped in recorded, (
        f"gemini_reasoner_model={shipped!r} matches no recorded answer "
        f"(they were recorded under {sorted(recorded)}). "
        "CACHE_MODE=replay forge ask will raise FixtureMiss on a clean clone."
    )
