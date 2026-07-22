"""The offline demo rests entirely on this layer, so it gets tested first."""

from __future__ import annotations

import json

import pytest

from forge.cache import FixtureCache, FixtureMiss, fixture_key
from forge.config import CacheMode


def _cache(settings, mode: CacheMode) -> FixtureCache:
    return FixtureCache(settings.fixtures_dir, mode, settings)


def test_records_then_replays_without_calling_again(settings):
    calls = []

    def perform():
        calls.append(1)
        return {"value": 42}

    recorded = _cache(settings, CacheMode.AUTO).call("demo.ns", {"q": "parse_config"}, perform)
    replayed = _cache(settings, CacheMode.REPLAY).call("demo.ns", {"q": "parse_config"}, perform)

    assert recorded == replayed == {"value": 42}
    assert len(calls) == 1, "replay must not invoke the underlying call"


def test_replay_miss_is_fatal(settings):
    def perform():
        raise AssertionError("must never run in replay mode")

    with pytest.raises(FixtureMiss, match="CACHE_MODE=replay"):
        _cache(settings, CacheMode.REPLAY).call("demo.ns", {"q": "absent"}, perform)


def test_refresh_overwrites_an_existing_fixture(settings):
    _cache(settings, CacheMode.AUTO).call("demo.ns", {"q": "x"}, lambda: {"v": "old"})
    _cache(settings, CacheMode.REFRESH).call("demo.ns", {"q": "x"}, lambda: {"v": "new"})

    assert _cache(settings, CacheMode.REPLAY).read("demo.ns", {"q": "x"}) == {"v": "new"}


def test_key_is_stable_across_dict_ordering(settings):
    assert fixture_key("ns", {"a": 1, "b": 2}) == fixture_key("ns", {"b": 2, "a": 1})


def test_key_separates_namespaces(settings):
    assert fixture_key("ns.one", {"a": 1}) != fixture_key("ns.two", {"a": 1})


def test_namespace_maps_to_nested_directories(settings):
    cache = _cache(settings, CacheMode.AUTO)
    cache.call("llm.planner", {"q": "x"}, lambda: {"ok": True})

    written = list((settings.fixtures_dir / "llm" / "planner").glob("*.json"))
    assert len(written) == 1


def test_secrets_are_scrubbed_before_hitting_disk(settings):
    """Fixtures are committed to the repo, so a leaked key would be published."""
    cache = _cache(settings, CacheMode.AUTO)
    cache.call(
        "demo.ns",
        {"echo": f"key={settings.google_api_key}"},
        lambda: {"url": f"https://api.example/?apiKey={settings.google_api_key}"},
    )

    on_disk = next(settings.fixtures_dir.rglob("*.json")).read_text(encoding="utf-8")
    assert settings.google_api_key not in on_disk
    assert "***REDACTED***" in json.loads(on_disk)["response"]["url"]


def test_secrets_added_later_are_scrubbed_too(settings):
    """`secret_values` is derived from field names, not a hand-kept list."""
    cache = _cache(settings, CacheMode.AUTO)
    cache.call("demo.ns", {"q": "x"}, lambda: {"leak": settings.groq_api_key})

    on_disk = next(settings.fixtures_dir.rglob("*.json")).read_text(encoding="utf-8")
    assert settings.groq_api_key not in on_disk


def test_stats_distinguish_hits_from_recordings(settings):
    recording = _cache(settings, CacheMode.AUTO)
    recording.call("demo.ns", {"q": "x"}, lambda: {"v": 1})
    assert recording.stats == {"hit": 0, "recorded": 1, "miss": 0}

    replaying = _cache(settings, CacheMode.REPLAY)
    replaying.call("demo.ns", {"q": "x"}, lambda: {"v": 1})
    assert replaying.stats == {"hit": 1, "recorded": 0, "miss": 0}
