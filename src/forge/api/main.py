"""FastAPI surface — cahier §11, the full route table.

Stateless retrieval (``/v1/search``, ``/v1/ask``) sits alongside the session routes
that drive the graph: create a session, send it a message and watch the run stream
back over SSE, approve at a §5.5 gate, replay the history, read the counters.

The pairing that matters is ``/messages`` and ``/approve``. A message opens an SSE
stream which ends in one of three terminal frames — ``done``, ``error``, or
``interrupt``. The last means the graph hit a human gate and is checkpointed,
waiting; ``/approve`` resumes it with ``Command(resume=...)``, and the client opens a
new stream for what follows. That is what makes an approval survive a closed browser
tab, or an hour, or a process restart.

Retrieval resources — the Qdrant client, the embedder, the BM25 encoder — are built
once per process and shared. Embedded Qdrant permits a single client handle per path,
and rebuilding the embedder per request would reload the model weights every time.
"""

from __future__ import annotations

import os
from contextlib import suppress
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from forge.api.repos import NotSelectable, list_repos, resolve_selection
from forge.api.sessions import SessionInfo, SessionMetrics, Timer, get_store
from forge.api.streaming import StreamEvent, graph_events
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

# Open by default because the only client is the FORGE UI on another localhost port
# and this service holds no cookie or ambient credential — every route is either
# public or keyed by a session id the caller already has. Narrow it before this is
# ever exposed beyond localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
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


def reset_resources() -> None:
    """Drop the cached client, embedder and *settings*. For tests that move them.

    The settings object is cached alongside the client, and every route hands that
    cached copy down — including to the guardrail log, which keys on
    ``checkpoint_db``. A test that redirects the database and clears
    ``get_settings`` still gets the stale object from here, so its events land in
    the previous test's file and the assertion looks like a missing guardrail rather
    than a missing reset. The embedded Qdrant client is closed on the way out: one
    client per path per process, and leaking it locks the next test's directory.
    """
    client = _resources.get("client")
    if client is not None:
        with suppress(Exception):
            client.close()
    _resources.clear()


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


# --- sessions and streaming (cahier §11) ----------------------------------

# The graph a session runs, injectable so tests can supply a scripted one and so a
# key-less machine fails at *build* time with a typed error rather than mid-stream.
_graph_factory = None


def set_graph_factory(factory) -> None:
    """Override how a session's graph is built. ``factory(session, settings)``."""
    global _graph_factory
    _graph_factory = factory


def _build_graph(session, settings: Settings, checkpointer):
    if _graph_factory is not None:
        return _graph_factory(session, settings, checkpointer)
    from forge.config import LLMRole
    from forge.core.graph import build_default_retriever
    from forge.core.loop import build_change_graph
    from forge.llm.provider import build_llm

    # Share the cached client/embedder/encoder — an embedded Qdrant permits one
    # client per path per process, and /v1/search already holds it.
    resources = get_resources()
    return build_change_graph(
        planner_llm=build_llm(LLMRole.REASONER, settings=settings),
        coder_llm=build_llm(LLMRole.CODER, settings=settings),
        reviewer_llm=build_llm(LLMRole.REASONER, settings=settings),
        workspace=session.workspace,
        settings=settings,
        checkpointer=checkpointer,
        retriever_node=build_default_retriever(
            settings,
            client=resources["client"],
            embedder=resources["embedder"],
            encoder=resources["encoder"],
        ),
    )


class CreateSession(BaseModel):
    session_id: str | None = None
    """Supply one to resume a known thread; omit for a fresh id."""


@app.post("/v1/sessions", response_model=SessionInfo, status_code=201, tags=["sessions"])
def create_session(request: CreateSession | None = None) -> SessionInfo:
    """Create a session and its git worktree (cahier §11)."""
    settings = get_settings()
    try:
        session = get_store(settings).create(request.session_id if request else None)
    except Exception as error:  # a worktree that cannot be made is a 400, not a 500
        raise HTTPException(
            status_code=400, detail=f"could not create a workspace: {error}"
        ) from error
    return session.info()


@app.get("/v1/sessions", response_model=list[SessionInfo], tags=["sessions"])
def list_sessions() -> list[SessionInfo]:
    return [session.info() for session in get_store(get_settings()).list()]


@app.delete("/v1/sessions/{session_id}", status_code=204, tags=["sessions"])
def close_session(session_id: str) -> None:
    """Remove the worktree. The conversation survives in the checkpointer."""
    if not get_store(get_settings()).close(session_id):
        raise HTTPException(status_code=404, detail=f"no such session: {session_id}")


def _require_session(session_id: str):
    session = get_store(get_settings()).get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"no such session: {session_id}")
    return session


class MessageRequest(BaseModel):
    message: str


@app.post("/v1/sessions/{session_id}/messages", tags=["sessions"])
async def send_message(session_id: str, request: MessageRequest):
    """Send a message; the run streams back as SSE (cahier §11).

    The stream is the response — there is no JSON body to wait for. It ends on
    ``done``, ``error``, or ``interrupt``; the last means a §5.5 gate is waiting and
    the caller should POST to ``/approve``.
    """
    from langchain_core.messages import HumanMessage

    session = _require_session(session_id)
    settings = get_settings()

    decision = check_input(request.message, session_id=session_id, settings=settings)
    if not decision:
        session.metrics.errors += 1
        raise HTTPException(status_code=400, detail=decision.reason)

    config = {"configurable": {"thread_id": session_id}}
    payload = {
        "messages": [HumanMessage(content=decision.text)],
        "session_id": session_id,
    }

    async def stream():
        from forge.core.checkpoint import sqlite_checkpointer

        with Timer() as timer:
            try:
                async with sqlite_checkpointer(settings.checkpoint_db) as checkpointer:
                    graph = _build_graph(session, settings, checkpointer)
                    async for event in graph_events(graph, payload, config):
                        yield event.sse()
            except Exception as error:  # noqa: BLE001 — build failures end the stream typed
                session.metrics.errors += 1
                yield StreamEvent(
                    type="error", data={"message": f"{type(error).__name__}: {error}"[:500]}
                ).sse()
        session.metrics.record_turn(duration_ms=timer.ms)

    return EventSourceResponse(stream())


class ApproveRequest(BaseModel):
    approved: bool = True
    feedback: str = ""


@app.post("/v1/sessions/{session_id}/approve", tags=["sessions"])
async def approve(session_id: str, request: ApproveRequest):
    """Resume after an ``interrupt()`` — the §5.5 gates' HTTP surface.

    Resumes with ``Command(resume=...)`` and streams whatever follows, so approving a
    plan flows straight into the run it unblocks.
    """
    from langgraph.types import Command

    session = _require_session(session_id)
    settings = get_settings()
    config = {"configurable": {"thread_id": session_id}}
    resume = Command(resume={"approved": request.approved, "feedback": request.feedback})

    async def stream():
        from forge.core.checkpoint import sqlite_checkpointer

        with Timer() as timer:
            try:
                async with sqlite_checkpointer(settings.checkpoint_db) as checkpointer:
                    graph = _build_graph(session, settings, checkpointer)
                    async for event in graph_events(graph, resume, config):
                        yield event.sse()
            except Exception as error:  # noqa: BLE001
                session.metrics.errors += 1
                yield StreamEvent(
                    type="error", data={"message": f"{type(error).__name__}: {error}"[:500]}
                ).sse()
        session.metrics.record_turn(duration_ms=timer.ms)

    return EventSourceResponse(stream())


class HistoryTurn(BaseModel):
    role: str
    content: str


class SessionHistory(BaseModel):
    session_id: str
    messages: list[HistoryTurn] = Field(default_factory=list)
    awaiting_approval: bool = False
    halted: str = ""


@app.get("/v1/sessions/{session_id}/history", response_model=SessionHistory, tags=["sessions"])
async def history(session_id: str) -> SessionHistory:
    """Replay the conversation from the checkpointer (cahier §11).

    Read from the checkpoint rather than from memory, so it works after a restart —
    which is the whole point of persisting it.
    """
    from forge.core.checkpoint import sqlite_checkpointer

    settings = get_settings()
    config = {"configurable": {"thread_id": session_id}}
    async with sqlite_checkpointer(settings.checkpoint_db) as checkpointer:
        snapshot = await checkpointer.aget_tuple(config)

    if snapshot is None:
        return SessionHistory(session_id=session_id)
    values = snapshot.checkpoint.get("channel_values", {})
    messages = []
    for message in values.get("messages", []) or []:
        content = getattr(message, "content", "")
        role = getattr(message, "type", "") or message.__class__.__name__
        messages.append(HistoryTurn(role=str(role), content=str(content)))
    return SessionHistory(
        session_id=session_id,
        messages=messages,
        awaiting_approval=bool(values.get("approvals") is not None and snapshot.metadata),
        halted=str(values.get("halted", "") or ""),
    )


# --- indexing (cahier §11) -------------------------------------------------


class IndexRequest(BaseModel):
    path: str | None = None
    incremental: bool = True


class IndexAccepted(BaseModel):
    status: str
    path: str
    incremental: bool


@app.post("/v1/index", response_model=IndexAccepted, status_code=202, tags=["ops"])
def start_index(request: IndexRequest, background: BackgroundTasks) -> IndexAccepted:
    """Kick off an indexing run in the background (202, not 200 — it is not done).

    Folded into ``api`` rather than a separate indexer service: descope §5 dropped
    that container, and a background task is what replaced it.
    """
    settings = get_settings()
    target = request.path or str(settings.target_repo)
    # Share the cached client/embedder. An *embedded* Qdrant allows one client per path
    # per process, and this process already holds one for /v1/search — letting
    # ``index_repo`` open its own raised "Storage folder is already accessed by another
    # instance" and killed the task. Unreachable until the UI could call this route.
    resources = get_resources()

    def run_index() -> None:
        from pathlib import Path

        from forge.rag.ingest import index_repo

        # `full` is the inverse of `incremental`: the default reindexes only what the
        # git diff touched, which is the §14/J2 2 s reindex the CLI already relies on.
        index_repo(
            Path(target),
            settings=settings,
            client=resources["client"],
            embedder=resources["embedder"],
            full=not request.incremental,
        )

    background.add_task(run_index)
    return IndexAccepted(status="accepted", path=target, incremental=request.incremental)


# --- choosing the target repository (D15b, Tier 2) -------------------------


class RepoOptionOut(BaseModel):
    name: str
    path: str
    is_git: bool
    is_current: bool


class TargetRequest(BaseModel):
    path: str
    """Must equal an entry from ``GET /v1/repos``. Not a path the server will trust."""


class TargetResponse(BaseModel):
    target_repo: str
    indexed: bool
    """False means retrieval still holds the *previous* repository's chunks."""
    active_sessions: int


@app.get("/v1/repos", response_model=list[RepoOptionOut], tags=["ops"])
def list_target_repos() -> list[RepoOptionOut]:
    """The repositories the UI may select — enumerated server-side from ``REPO_ROOTS``.

    This list *is* the permission. ``POST /v1/target`` accepts nothing that is not in
    it, so what this route returns is the exact reach a browser has.
    """
    return [RepoOptionOut(**vars(option)) for option in list_repos(get_settings())]


@app.post("/v1/target", response_model=TargetResponse, tags=["ops"])
def set_target_repo(request: TargetRequest) -> TargetResponse:
    """Point FORGE at another allowlisted repository, at runtime.

    ``target_repo`` is the confinement root for the file tools, so this route is a
    security boundary and is written as one: the path is re-validated against a fresh
    enumeration (never a cached one), the decision is a §8.5 event either way, and a
    refusal is a 400 carrying the guardrail's own sentence rather than a traceback.

    Switching invalidates process state that was built for the old repository — the
    cached settings, the Qdrant client, the embedder and the BM25 encoder — so
    ``reset_resources()`` drops all of it and the next request rebuilds. It does
    **not** reindex: the response says whether an index exists, because a silent
    switch that left the previous repository's chunks in place would answer questions
    about the wrong codebase without any error at all.
    """
    settings = get_settings()
    log = get_log(settings)
    try:
        target = resolve_selection(request.path, settings)
    except NotSelectable as refusal:
        log.emit(
            stage=GuardrailStage.POLICY,
            rule="policy.target_denied",
            action=GuardrailAction.BLOCKED,
            detail=str(refusal),
            target=request.path,
        )
        raise HTTPException(
            status_code=400,
            detail=f"{request.path!r} is not a selectable repository. See GET /v1/repos.",
        ) from refusal

    log.emit(
        stage=GuardrailStage.POLICY,
        rule="policy.target_switch",
        action=GuardrailAction.ALLOWED,
        detail=f"target repo set to {target}",
        target=str(target),
    )

    # Settings are read once per process from the environment and cached, so the
    # environment is what has to change for the reread to see anything.
    os.environ["TARGET_REPO"] = str(target)
    get_settings.cache_clear()
    reset_resources()

    fresh = get_settings()
    try:
        indexed = store.count(get_resources()["client"], store.CODE_COLLECTION) > 0
    except Exception:  # noqa: BLE001 — "no collection yet" is an answer, not a failure
        indexed = False
    return TargetResponse(
        target_repo=str(fresh.target_repo),
        indexed=indexed,
        # Sessions created against the old repo hold worktrees cut from it. They are
        # not invalidated here — reporting the count lets the UI say so rather than
        # having the user discover it when a patch applies to the wrong tree.
        active_sessions=len(get_store(fresh).list()),
    )


# --- metrics (cahier §11) --------------------------------------------------


class MetricsResponse(BaseModel):
    sessions: int
    totals: SessionMetrics
    per_session: dict[str, SessionMetrics] = Field(default_factory=dict)
    guardrail_events: int = 0


@app.get("/v1/metrics", response_model=MetricsResponse, tags=["ops"])
def metrics(session_id: str | None = None) -> MetricsResponse:
    """Cost, latency and token counters — per session, and summed."""
    store_ = get_store(get_settings())
    sessions = [s for s in store_.list() if session_id is None or s.session_id == session_id]

    log = get_log(get_settings())
    totals = SessionMetrics()
    for session in sessions:
        # The counter lives in the guardrail log, not on the session — nothing was
        # filling this field in, so a per-session view reported 0 while the log itself
        # had events to show. Read it from the one place that actually knows.
        session.metrics.guardrail_events = log.count(session_id=session.session_id)
        totals.turns += session.metrics.turns
        totals.llm_calls += session.metrics.llm_calls
        totals.tokens += session.metrics.tokens
        totals.errors += session.metrics.errors
        totals.guardrail_events += session.metrics.guardrail_events
        totals.latency_ms_total += session.metrics.latency_ms_total
        totals.latency_ms_last = session.metrics.latency_ms_last or totals.latency_ms_last

    return MetricsResponse(
        sessions=len(sessions),
        totals=totals,
        per_session={s.session_id: s.metrics for s in sessions},
        guardrail_events=log.count(session_id=session_id),
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
    against what was actually retrieved before the answer leaves the process. The
    §8.2 indirect-injection scan sits between them, inside ``answer_question``, where
    the retrieved chunks become prompt — the sentinels guard the *user's* text, and a
    poisoned repository comment never passes through either of them.
    """
    resources = get_resources()
    settings = resources["settings"]

    decision = check_input(request.question, session_id=request.session_id, settings=settings)
    if not decision:
        # A refusal is a 400 with the guardrail's own sentence, not a 500 and not a
        # silent empty answer — the event is already in the log either way.
        raise HTTPException(status_code=400, detail=decision.reason)

    # An ask turn spends a model call like any other, so it is counted like any other.
    # Without this the Cost panel reads all zeros for a session that only ever asked —
    # which reads as "free", not as "not measured".
    session = get_store(settings).get(request.session_id) if request.session_id else None
    with Timer() as timer:
        try:
            answer = answer_question(
                decision.text,
                k=request.k,
                settings=settings,
                client=resources["client"],
                embedder=resources["embedder"],
                encoder=resources["encoder"],
                session_id=request.session_id,
            )
        except Exception:
            if session:
                session.metrics.errors += 1
            raise
    if session:
        session.metrics.record_turn(duration_ms=timer.ms, llm_calls=1)
    # pack=None: this path verifies citations inside answer_question and does not
    # hand the pack back, so sentinel_out scans for secrets and leaves them alone
    # rather than re-checking against a pack it does not have.
    return check_answer(answer, None, session_id=request.session_id).answer


# --- the built SPA (cahier §10.1 / L4) -------------------------------------


# `docker compose up` has to produce something a person can open, and §15.6 is a
# *browser* scenario — an API with no UI cannot satisfy C9 and C7 at once. Serving
# `web/dist` from this process keeps it to one origin and one port, so the SPA needs
# no CORS grant and no reverse proxy. Mounted last on purpose: every /v1 route above
# is already registered, so the catch-all can only claim paths nothing else wanted.
#
# Absent in a dev checkout that has never run `npm run build`, and that is fine —
# `npm run dev` serves the UI on :5173 and proxies /v1 here.
def _mount_web_ui() -> Path | None:
    candidates = [
        Path(os.environ["FORGE_WEB_DIST"]) if os.environ.get("FORGE_WEB_DIST") else None,
        Path("/app/web/dist"),
        Path(__file__).resolve().parents[3] / "web" / "dist",
    ]
    for dist in candidates:
        if dist and (dist / "index.html").is_file():
            app.mount("/", StaticFiles(directory=str(dist), html=True), name="web")
            return dist
    return None


WEB_DIST = _mount_web_ui()
