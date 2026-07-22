from __future__ import annotations

import pytest

from forge.cache import reset_cache
from forge.config import CacheMode, Settings, get_settings


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
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


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
