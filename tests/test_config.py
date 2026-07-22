"""Settings drive quota use and the offline guarantee, so they get asserted."""

from __future__ import annotations

import pytest

from forge.config import CacheMode, LLMProvider, LLMRole, Settings


def test_model_name_resolves_per_provider_and_role(settings):
    for provider in LLMProvider:
        configured = settings.model_copy(update={"llm_provider": provider})
        for role in LLMRole:
            assert configured.model_name(role), f"{provider}/{role} has no model configured"


def test_offline_is_exactly_replay_mode(settings):
    assert settings.model_copy(update={"cache_mode": CacheMode.REPLAY}).offline is True
    assert settings.model_copy(update={"cache_mode": CacheMode.AUTO}).offline is False
    assert settings.model_copy(update={"cache_mode": CacheMode.REFRESH}).offline is False


def test_secret_values_finds_credentials_without_a_hand_kept_list(settings):
    assert set(settings.secret_values()) == {
        settings.google_api_key,
        settings.groq_api_key,
    }


def test_secret_values_skips_blanks(settings):
    """An unset key must not put "" into the scrub list — it would match everywhere."""
    assert "" not in Settings(_env_file=None).secret_values()


def test_reranker_defaults_off():
    """docs/descope-v1.md §3 — measured in the eval harness, off in the live path."""
    assert Settings(_env_file=None).rerank_enabled is False


@pytest.mark.parametrize(
    "field, ceiling",
    [("sandbox_memory_mb", 512), ("sandbox_pids_limit", 128), ("sandbox_timeout_s", 60)],
)
def test_sandbox_caps_stay_within_the_documented_hardening(field, ceiling):
    """Cahier 8.3 quotes these numbers; drift would make the security slide false."""
    assert getattr(Settings(_env_file=None), field) == ceiling
