"""Execute a retrieval configuration over the golden set and score it.

Shared by ``evals/run_retrieval.py`` (the one live baseline config) and
``evals/run_ablation.py`` (the five §13.1 configs). Keeping the loop here means
both scripts score with the *same* metric and differ only in which configurations
they hand it — the whole point of an ablation is that only the varied knob moves.

The golden set keys each answer by AST ``chunk_id``. ``build_span_index`` resolves
those to ``(path, line-span)`` from the AST collection once, so a config whose
retrieved chunks carry *different* ids — the naive-chunked row — is still scored
against the same answers, by overlap. See ``evaluation.metrics`` for why overlap.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from qdrant_client import QdrantClient

from forge.config import PROJECT_ROOT, Settings, get_settings
from forge.evaluation.metrics import (
    DEFAULT_KS,
    Span,
    aggregate,
    coverage_recall_hit,
    percentile,
    score_query,
)
from forge.models import Chunk, SearchHit
from forge.rag import store
from forge.rag.callgraph import build_symbol_index, resolve_callees
from forge.rag.embed import Embedder
from forge.rag.pack import expand_hits, expand_hits_with_calls, group_key, load_groups
from forge.rag.rerank import Reranker
from forge.rag.retrieve import Filters, SearchMode, hybrid_search, load_encoder
from forge.rag.sparse import BM25Encoder

GOLDEN = PROJECT_ROOT / "evals" / "golden" / "code.jsonl"

# Candidate depth handed to fusion/rerank before the metric slices its top-k. Deep
# enough that reranking has material to reorder and parent expansion has siblings
# to pull in; the reported numbers are all @5 / @10.
FETCH_DEPTH = 30


def load_golden(path: Path = GOLDEN) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def build_span_index(
    client: QdrantClient, collection: str = store.CODE_COLLECTION
) -> dict[str, Span]:
    """``chunk_id → Span`` over a collection, for resolving golden answers.

    Read from the AST collection, whose ids the golden set uses. One scroll; the
    map is reused across every query and every config in a run.
    """
    index: dict[str, Span] = {}
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection, with_payload=True, limit=1024, offset=offset
        )
        for point in points:
            if point.payload:
                chunk = store.chunk_from_payload(point.payload)
                index[chunk.chunk_id] = Span(chunk.path, chunk.start_line, chunk.end_line)
        if offset is None:
            break
    return index


def golden_spans(item: dict[str, Any], span_index: dict[str, Span]) -> list[Span]:
    return [span_index[cid] for cid in item["relevant"] if cid in span_index]


@dataclass(frozen=True)
class RetrievalConfig:
    """One row of the ablation: which collection, which retrievers, rerank on/off,
    parent expansion on/off. ``embedder_model`` records the embedder the collection
    was built with, for the table's provenance."""

    name: str
    collection: str = store.CODE_COLLECTION
    mode: SearchMode = "hybrid"
    rerank: bool = False
    parent_expand: bool = False
    expand_calls: bool = False
    """O7 — add the definition of what each matched chunk calls, one hop out. Scored
    like ``parent_expand``: what the pack contains at rank k, not extra ranking slots."""
    embedder_model: str = ""
    filters: Filters = field(default_factory=Filters)
    tag: str = "canonical"
    """Which table the row belongs to: ``canonical`` (the §13.1 five), ``diagnostic``
    (levers like test-demotion), or ``bge`` (the embedder decision)."""


def _retrieve(
    cfg: RetrievalConfig,
    query: str,
    *,
    settings: Settings,
    client: QdrantClient,
    embedder: Embedder,
    encoder: BM25Encoder,
    reranker: Reranker | None,
    repo: str | Path | None,
) -> tuple[list[SearchHit], float]:
    """Run one query under ``cfg`` (search → optional rerank) and time it."""
    started = time.perf_counter()
    hits = hybrid_search(
        query,
        k=FETCH_DEPTH,
        filters=cfg.filters,
        settings=settings,
        client=client,
        embedder=embedder,
        encoder=encoder,
        repo=repo,
        collection=cfg.collection,
        mode=cfg.mode,
    )
    if cfg.rerank and hits:
        if reranker is None:
            raise ValueError(f"config {cfg.name!r} needs a reranker but none was provided")
        hits = reranker.rerank(query, hits, top_k=len(hits))
    return hits, time.perf_counter() - started


def _span(chunk: Chunk) -> Span:
    return Span(chunk.path, chunk.start_line, chunk.end_line)


def evaluate_config(
    cfg: RetrievalConfig,
    golden: list[dict[str, Any]],
    span_index: dict[str, Span],
    *,
    settings: Settings | None = None,
    client: QdrantClient,
    embedder: Embedder,
    encoder: BM25Encoder | None = None,
    reranker: Reranker | None = None,
    groups: dict[tuple[str, str], list[Chunk]] | None = None,
    ks: tuple[int, ...] = DEFAULT_KS,
    repo: str | Path | None = None,
) -> dict[str, Any]:
    """Score every golden query under ``cfg`` and aggregate, with latency percentiles.

    Precision / nDCG / MRR are measured on the base ranking of matched chunks. For
    the parent-expansion row, Recall / Hit are measured as *coverage* of the top-k
    hits' parent-document groups (``coverage_recall_hit``) — the mechanism the
    packer ships — and the cost is reported as ``mean_chunks`` / ``mean_tokens``,
    not as lost ranking slots. For every other row each "group" is the single
    chunk, so coverage recall is exactly Recall@k.
    """
    settings = settings or get_settings()
    encoder = encoder or load_encoder(settings, cfg.collection)
    if cfg.parent_expand and groups is None:
        groups = load_groups(client, cfg.collection)
    symbol_index = build_symbol_index(groups) if (cfg.expand_calls and groups) else None

    per_query: list[dict[str, Any]] = []
    latencies: list[float] = []
    for item in golden:
        relevant = golden_spans(item, span_index)
        hits, dt = _retrieve(
            cfg,
            item["question"],
            settings=settings,
            client=client,
            embedder=embedder,
            encoder=encoder,
            reranker=reranker,
            repo=repo,
        )
        latencies.append(dt)

        record = {"id": item.get("id"), "question": item["question"]}
        record.update(score_query([_span(h.chunk) for h in hits], relevant, ks=ks))

        if cfg.parent_expand:
            groups_per_rank = [
                [
                    _span(c)
                    for c in (groups.get(group_key(h.chunk)) or [h.chunk])
                    + (resolve_callees(h.chunk, symbol_index) if symbol_index else [])
                ]
                for h in hits
            ]
            for k in ks:
                recall, hit = coverage_recall_hit(groups_per_rank, relevant, k)
                record[f"recall@{k}"], record[f"hit@{k}"] = recall, hit
            packed = (
                expand_hits_with_calls(hits[: max(ks)], groups or {})
                if cfg.expand_calls
                else expand_hits(hits[: max(ks)], groups or {})
            )
        else:
            packed = [h.chunk for h in hits[: max(ks)]]
        record["chunks"] = float(len(packed))
        record["tokens"] = float(sum(max(len(c.raw) // 4, 1) for c in packed))
        per_query.append(record)

    n = len(per_query) or 1
    summary: dict[str, Any] = aggregate(per_query, ks=ks)
    summary.update(
        name=cfg.name,
        tag=cfg.tag,
        collection=cfg.collection,
        embedder=cfg.embedder_model or embedder.name,
        mode=cfg.mode,
        rerank=cfg.rerank,
        parent_expand=cfg.parent_expand,
        expand_calls=cfg.expand_calls,
        prefer_implementation=cfg.filters.prefer_implementation,
        p50_ms=percentile(latencies, 50) * 1000,
        p95_ms=percentile(latencies, 95) * 1000,
        mean_chunks=sum(q["chunks"] for q in per_query) / n,
        mean_tokens=sum(q["tokens"] for q in per_query) / n,
        per_query=per_query,
    )
    return summary
