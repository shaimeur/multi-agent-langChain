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
