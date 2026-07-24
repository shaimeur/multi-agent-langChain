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

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from forge.config import Settings, get_settings
from forge.guardrails.events import get_log
from forge.guardrails.sentinel_in import check_input
from forge.guardrails.sentinel_out import check_answer
from forge.models import GroundedAnswer, GuardrailAction, GuardrailEvent, GuardrailStage
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


# --- guardrails (cahier §8.5) ---------------------------------------------


@app.get("/v1/guardrails/events", response_model=list[GuardrailEvent], tags=["guardrails"])
def guardrail_events(
    session_id: str | None = None,
    stage: GuardrailStage | None = None,
    action: GuardrailAction | None = None,
    limit: int = 200,
) -> list[GuardrailEvent]:
    """Every guardrail decision, newest first — the §8.5 deliverable.

    This route is the difference between "we have guardrails" and "here are the 47
    guardrail events from this session", which is why it is queryable by session,
    stage and action rather than being a dump.
    """
    return get_log(get_settings()).events(
        session_id=session_id, stage=stage, action=action, limit=limit
    )


class GuardrailSummary(BaseModel):
    total: int
    by_rule: dict[str, int]


@app.get("/v1/guardrails/summary", response_model=GuardrailSummary, tags=["guardrails"])
def guardrail_summary(session_id: str | None = None) -> GuardrailSummary:
    """Counts per rule — the shape the security slide is built from."""
    log = get_log(get_settings())
    by_rule = log.counts_by_rule(session_id=session_id)
    return GuardrailSummary(total=sum(by_rule.values()), by_rule=by_rule)


class AskRequest(BaseModel):
    question: str
    k: int = 8
    session_id: str = ""
    """Attributes guardrail events to a session, so the §8.5 log can be filtered."""


@app.post("/v1/ask", response_model=GroundedAnswer, tags=["retrieval"])
def ask(request: AskRequest) -> GroundedAnswer:
    """Grounded answer with citations verified in code. Needs a configured LLM.

    Wrapped by both sentinels (cahier §8): ``sentinel_in`` validates and redacts the
    question before it reaches retrieval, ``sentinel_out`` re-verifies every citation
    against what was actually retrieved before the answer leaves the process.
    """
    resources = get_resources()
    settings = resources["settings"]

    decision = check_input(request.question, session_id=request.session_id, settings=settings)
    if not decision:
        # A refusal is a 400 with the guardrail's own sentence, not a 500 and not a
        # silent empty answer — the event is already in the log either way.
        raise HTTPException(status_code=400, detail=decision.reason)

    answer = answer_question(
        decision.text,
        k=request.k,
        settings=settings,
        client=resources["client"],
        embedder=resources["embedder"],
        encoder=resources["encoder"],
    )
    # pack=None: this path verifies citations inside answer_question and does not
    # hand the pack back, so sentinel_out scans for secrets and leaves them alone
    # rather than re-checking against a pack it does not have.
    return check_answer(answer, None, session_id=request.session_id).answer
