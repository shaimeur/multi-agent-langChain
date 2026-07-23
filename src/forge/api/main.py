"""FastAPI surface — cahier 11.

Health, plus the two retrieval routes that make FORGE a usable service today:
``POST /v1/search`` (hybrid retrieval, no LLM) and ``POST /v1/ask`` (grounded
answer with verified citations). The session, streaming and approval routes land
with the graph (D5-D9) and the SSE layer (D12).

Retrieval resources — the Qdrant client, the embedder, the BM25 encoder — are
built once per process and shared across requests. Embedded Qdrant permits a
single client handle per path, and rebuilding the embedder per request would
reload the model weights every time; this is the shared-client pattern D3's
STATE note calls for.
"""

from __future__ import annotations

from fastapi import FastAPI
from pydantic import BaseModel

from forge.config import Settings, get_settings
from forge.models import GroundedAnswer
from forge.rag import store
from forge.rag.answer import answer_question
from forge.rag.embed import build_embedder
from forge.rag.retrieve import Filters, hybrid_search, is_identifier_query, load_encoder

app = FastAPI(
    title="FORGE",
    description="Multi-agent engineering assistant — grounded code RAG, "
    "planned patches, sandbox-verified tests.",
    version="0.1.0",
)

_resources: dict = {}


def get_resources() -> dict:
    """Lazily build and cache the shared retrieval resources."""
    if "client" not in _resources:
        settings = get_settings()
        _resources.update(
            settings=settings,
            client=store.build_client(settings),
            embedder=build_embedder(settings),
            encoder=load_encoder(settings),
        )
    return _resources


# --- health ---------------------------------------------------------------


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


# --- retrieval ------------------------------------------------------------


class SearchRequest(BaseModel):
    query: str
    k: int = 8
    language: str | None = None
    path: str | None = None


class Hit(BaseModel):
    path: str
    start_line: int
    end_line: int
    symbol: str | None
    kind: str
    retrievers: str
    score: float


class SearchResponse(BaseModel):
    query: str
    route: str
    hits: list[Hit]


@app.post("/v1/search", response_model=SearchResponse, tags=["retrieval"])
def search(request: SearchRequest) -> SearchResponse:
    """Hybrid retrieval over the indexed repo. No LLM — always available."""
    resources = get_resources()
    hits = hybrid_search(
        request.query,
        k=request.k,
        filters=Filters(language=request.language, path_prefix=request.path),
        settings=resources["settings"],
        client=resources["client"],
        embedder=resources["embedder"],
        encoder=resources["encoder"],
    )
    route = "lexical" if is_identifier_query(request.query) else "semantic"
    return SearchResponse(
        query=request.query,
        route=route,
        hits=[
            Hit(
                path=h.chunk.path,
                start_line=h.chunk.start_line,
                end_line=h.chunk.end_line,
                symbol=h.chunk.symbol,
                kind=h.chunk.kind.value,
                retrievers=h.provenance,
                score=h.score,
            )
            for h in hits
        ],
    )


class AskRequest(BaseModel):
    question: str
    k: int = 8


@app.post("/v1/ask", response_model=GroundedAnswer, tags=["retrieval"])
def ask(request: AskRequest) -> GroundedAnswer:
    """Grounded answer with citations verified in code. Needs a configured LLM."""
    resources = get_resources()
    return answer_question(
        request.question,
        k=request.k,
        settings=resources["settings"],
        client=resources["client"],
        embedder=resources["embedder"],
        encoder=resources["encoder"],
    )
