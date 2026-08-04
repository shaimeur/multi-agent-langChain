"""RETRIEVER node — all knowledge access, wrapping D3/D4 retrieval (cahier §4/A1).

Runs the D4 pipeline *live*: hybrid dense+BM25 with RRF, test-demotion
(`prefer_implementation`), and parent-document expansion under a token budget, then
hands the answerer a ``ContextPack``. On re-invocation it returns only the chunks
not already in the working set — the *differential* pack of A1 — so a
needs_more_context loop (D6) grows the context instead of re-sending it. No LLM: the
query-rewrite branch of the §6.5 gate stays deferred; a natural-language question is
issued verbatim, which is the honest behaviour until the graph adds rewriting.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from qdrant_client import QdrantClient

from forge.config import Settings
from forge.core.agents.base import latest_user_text
from forge.core.state import ForgeState
from forge.guardrails.events import get_log
from forge.guardrails.injection import scan_chunks
from forge.rag import store
from forge.rag.embed import Embedder
from forge.rag.pack import DEFAULT_TOKEN_BUDGET, load_groups, pack_context
from forge.rag.retrieve import Filters, hybrid_search, load_encoder
from forge.rag.sparse import BM25Encoder


def make_retriever_node(
    *,
    settings: Settings,
    client: QdrantClient,
    embedder: Embedder,
    encoder: BM25Encoder | None = None,
    repo: str | Path | None = None,
    collection: str = store.CODE_COLLECTION,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
) -> Callable[[ForgeState], dict]:
    """Build the retriever node over shared, already-loaded resources."""

    def _field(value, name):
        """A resumed checkpoint hands the plan back as a dict; read either shape."""
        if value is None:
            return ""
        got = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        return got or ""

    encoder = encoder or load_encoder(settings, collection)
    repo = repo if repo is not None else settings.target_repo
    groups: dict | None = None  # parent-document groups, loaded once on first use

    def retriever_node(state: ForgeState) -> dict:
        nonlocal groups
        question = latest_user_text(state)
        # §5.1 — the planner sends the turn back here when the pack was insufficient,
        # and ``missing`` says what it could not find. Searching the original question
        # again returns the identical pack, so the re-entry is dead by construction:
        # the planner sees what it already rejected and gives up. Appending rather than
        # replacing keeps the bug report's own vocabulary in the query — ``missing``
        # names a symbol, the report names the symptom, and the fix site can match either.
        plan = state.get("plan")
        missing = _field(plan, "missing") if _field(plan, "needs_more_context") else ""
        query = f"{question}\n{missing}".strip() if missing else question
        hits = hybrid_search(
            query,
            filters=Filters(prefer_implementation=settings.prefer_implementation),
            settings=settings,
            client=client,
            embedder=embedder,
            encoder=encoder,
            repo=repo,
            collection=collection,
        )
        if groups is None:
            groups = load_groups(client, collection)
        pack = pack_context(
            hits,
            groups=groups,
            token_budget=token_budget,
            expand_calls=settings.retrieval_expand_calls,
            queries=[query],
        )

        # §8.2 — this is where third-party text enters the prompt, so this is where
        # the indirect-injection scan belongs. Every chunk is scanned and neutralised
        # *before* the pack reaches the planner or the answerer; each finding is an
        # event, and a clean chunk is logged as clean.
        safe_chunks, _ = scan_chunks(
            pack.chunks, session_id=state.get("session_id", ""), log=get_log(settings)
        )
        pack = pack.model_copy(update={"chunks": safe_chunks})

        seen = {c.chunk_id for c in state.get("chunks", [])}
        fresh = [c for c in pack.chunks if c.chunk_id not in seen]
        # `pack` is the full context the answerer reads; `chunks` accumulates the
        # session working set, deduped by the merge_chunks reducer.
        return {"pack": pack, "chunks": fresh}

    return retriever_node
