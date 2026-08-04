from __future__ import annotations

import pytest

from forge.cache import reset_cache
from forge.config import CacheMode, Settings, get_settings
from forge.guardrails.events import reset_log


@pytest.fixture(autouse=True)
def _isolate_global_settings(tmp_path, monkeypatch):
    """No test may reach the network or touch the developer's real index.

    `get_settings()` is cached and reads the real .env, so anything invoking a
    code path that calls it — the CLI, the API — would otherwise pick up
    EMBEDDING_MODEL=BAAI/bge-m3 and quietly download 2 GB of weights mid-suite.
    Environment variables win over the .env file, so setting them here is enough.
    """
    monkeypatch.setenv("CACHE_MODE", "replay")
    monkeypatch.setenv("EMBEDDING_MODEL", "hashing")
    monkeypatch.setenv("QDRANT_URL", "")
    monkeypatch.setenv("QDRANT_PATH", str(tmp_path / "global-qdrant"))
    # …and no test may write into the real checkpoint database. It is not scratch
    # state: `notebooks/02_agent_traces.ipynb` (deliverable L3, gate C2) is built
    # from its checkpoints and guardrail events, so a suite run that appended a few
    # hundred synthetic events would quietly corrupt the evidence the notebook
    # presents. Guardrail logging now happens on the plain answer path too, which is
    # what made this reachable from tests that never mention a database.
    monkeypatch.setenv("CHECKPOINT_DB", str(tmp_path / "global-checkpoints.sqlite"))
    get_settings.cache_clear()
    reset_log()
    yield
    get_settings.cache_clear()
    reset_log()


@pytest.fixture
def settings(tmp_path) -> Settings:
    """Settings isolated from the developer's real .env and real fixture store."""
    return Settings(
        _env_file=None,
        cache_mode=CacheMode.AUTO,
        fixtures_dir=tmp_path / "fixtures",
        google_api_key="google-secret-key-123456",
        groq_api_key="groq-secret-key-123456",
    )


@pytest.fixture(autouse=True)
def _reset_cache_singleton():
    reset_cache()
    yield
    reset_cache()


@pytest.fixture(autouse=True)
def _reset_global_llm_cache():
    """`build_llm` installs a process-global LangChain cache and never removes it.

    Any test that builds a real model — or invokes a CLI command that does — would
    otherwise leave it installed, and every later test's FakeListChatModel would be
    routed through the fixture store and fail on a prompt nobody recorded. The
    symptom is a FixtureMiss in a completely unrelated file, which is a miserable
    thing to debug, so the leak is closed here rather than left to ordering luck.
    """
    from forge.llm.provider import reset_llm_cache

    reset_llm_cache()
    yield
    reset_llm_cache()
