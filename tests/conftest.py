from __future__ import annotations

import pytest

from forge.cache import reset_cache
from forge.config import CacheMode, Settings


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
