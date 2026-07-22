"""FastAPI surface — cahier 11.

Only the health probe is live at this stage; the session, message-streaming and
approval routes land with the graph (D5-D9) and the SSE layer (D12).
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from forge.config import Settings, get_settings

app = FastAPI(
    title="FORGE",
    description="Multi-agent engineering assistant — grounded code RAG, "
    "planned patches, sandbox-verified tests.",
    version="0.1.0",
)


class Health(BaseModel):
    status: str
    version: str
    llm_provider: str
    cache_mode: str
    offline: bool


def _health(settings: Settings) -> Health:
    return Health(
        status="ok",
        version=app.version,
        llm_provider=settings.llm_provider.value,
        cache_mode=settings.cache_mode.value,
        offline=settings.offline,
    )


@app.get("/v1/health", response_model=Health, tags=["ops"])
def health() -> Health:
    """Liveness probe. Also what the compose healthcheck calls."""
    return _health(get_settings())


@app.get("/health", response_model=Health, include_in_schema=False)
def health_unversioned() -> Health:
    """Convenience alias so a bare /health does not 404 during development."""
    return _health(get_settings())
