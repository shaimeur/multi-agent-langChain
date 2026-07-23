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
    encoder = encoder or load_encoder(settings, collection)
    repo = repo if repo is not None else settings.target_repo
    groups: dict | None = None  # parent-document groups, loaded once on first use

    def retriever_node(state: ForgeState) -> dict:
        nonlocal groups
        question = latest_user_text(state)
        hits = hybrid_search(
            question,
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
        pack = pack_context(hits, groups=groups, token_budget=token_budget, queries=[question])

        seen = {c.chunk_id for c in state.get("chunks", [])}
        fresh = [c for c in pack.chunks if c.chunk_id not in seen]
        # `pack` is the full context the answerer reads; `chunks` accumulates the
        # session working set, deduped by the merge_chunks reducer.
        return {"pack": pack, "chunks": fresh}

    return retriever_node
