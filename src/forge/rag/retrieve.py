"""Hybrid retrieval over the named dense + sparse vectors — cahier §6.5.

Dense answers *"where is authentication handled"*; sparse answers *"find every
parse_config"*. Neither wins everywhere, so both run and Reciprocal Rank Fusion
combines their rankings — score-free, so a cosine similarity and a BM25 weight
never have to be made commensurable. A third list, literal ripgrep matches mapped
back to the chunk that contains the line, joins the fusion for identifier-shaped
queries: the gate in descope §8.2 that sends a bare symbol straight to lexical
retrieval, because rewriting a literal identifier adds latency and *degrades*
precision.

The LLM query rewrite the gate's other branch would trigger is deferred to the
graph (D5+); until then a natural-language query is issued verbatim to dense +
sparse, which is the honest behaviour and already the DoD's baseline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from qdrant_client import QdrantClient, models

from forge.config import Settings, get_settings
from forge.models import Chunk, Retriever, SearchHit
from forge.rag import store
from forge.rag.embed import Embedder, build_embedder
from forge.rag.ingest import read_manifest
from forge.rag.sparse import BM25Encoder
from forge.tools.ripgrep import ripgrep_available, ripgrep_search

RRF_K = 60
"""RRF damping. The standard value; it flattens the gap between rank 1 and rank 2
so a chunk that several retrievers rank *consistently* outscores one that a single
retriever ranks first."""


_TEST_PATH = re.compile(r"(^|/)tests?/|(^|/)test_[^/]*\.py$|_test\.py$|(^|/)conftest\.py$")


def is_test_path(path: str) -> bool:
    """True for a test module — a ``tests/`` dir, ``test_*.py``, ``*_test.py``.

    The D3 finding: for *"how are comments stripped"* the five ``test_strip_*``
    functions match the query wording better than the ``StripCommentsFilter`` they
    exercise, and BM25 ranks them above it. ``prefer_implementation`` uses this to
    push tests below implementation after fusion — a targeted answer to the
    test-corpus pollution the ablation measures.
    """
    return bool(_TEST_PATH.search(path))


@dataclass(frozen=True)
class Filters:
    """Metadata narrowing — cahier §6.5. ``language`` is pushed into Qdrant;
    ``path_prefix`` is applied in Python because Qdrant has no native prefix
    match, and post-filtering a small fused set is simpler than a payload-index
    trick that behaves differently in embedded mode. ``prefer_implementation``
    demotes (never drops) test chunks below implementation in the fused ranking."""

    language: str | None = None
    path_prefix: str | None = None
    prefer_implementation: bool = False

    def to_qdrant(self) -> models.Filter | None:
        if not self.language:
            return None
        return models.Filter(
            must=[
                models.FieldCondition(key="language", match=models.MatchValue(value=self.language))
            ]
        )

    def keep(self, chunk: Chunk) -> bool:
        if self.language and chunk.language != self.language:
            return False
        return not (self.path_prefix and not chunk.path.startswith(self.path_prefix))


# A query is identifier-shaped when it is a single token that *looks* like code:
# snake_case, camelCase, a dotted/`::` path, or an initial-capital name. A bare
# lowercase word ("authentication") is a concept, not a symbol, and stays on the
# semantic path.
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:[.:]{1,2}[A-Za-z_][A-Za-z0-9_]*)*$")
_CODE_SHAPE = re.compile(r"[a-z][A-Z]|_|[.:]")


def is_identifier_query(query: str) -> bool:
    """True for ``parse_config`` / ``SessionManager.refresh``, false for prose.

    The gate of descope §8.2: these skip the (future) LLM rewrite and go straight
    to ripgrep + sparse. Precision, not just cost — an identifier's meaning is
    that it matches exactly.
    """
    q = query.strip()
    if not q or " " in q or not _IDENTIFIER.match(q):
        return False
    return bool(_CODE_SHAPE.search(q)) or q[0].isupper()


def load_encoder(settings: Settings, collection: str = store.CODE_COLLECTION) -> BM25Encoder:
    """The BM25 stats written at index time, or a bare encoder when none exist.

    Query encoding is presence-only (the IDF is baked into the *document*
    vectors), so a bare encoder still produces correct query terms; loading the
    fitted stats keeps this honest if that ever changes.
    """
    bm25 = read_manifest(settings, collection).get("bm25")
    return BM25Encoder.from_dict(bm25) if bm25 else BM25Encoder()


def _hits(points: list[models.ScoredPoint], retriever: Retriever, flt: Filters) -> list[SearchHit]:
    out: list[SearchHit] = []
    for point in points:
        if not point.payload:
            continue
        chunk = store.chunk_from_payload(point.payload)
        if not flt.keep(chunk):
            continue
        out.append(
            SearchHit(
                chunk=chunk,
                score=point.score,
                retrievers=[retriever],
                component_scores={retriever.value: point.score},
            )
        )
    return out


def dense_search(
    client: QdrantClient,
    embedder: Embedder,
    query: str,
    k: int,
    flt: Filters,
    collection: str = store.CODE_COLLECTION,
) -> list[SearchHit]:
    response = client.query_points(
        collection_name=collection,
        query=embedder.embed_query(query),
        using=store.DENSE,
        limit=k,
        query_filter=flt.to_qdrant(),
        with_payload=True,
    )
    return _hits(response.points, Retriever.DENSE, flt)


def sparse_search(
    client: QdrantClient,
    encoder: BM25Encoder,
    query: str,
    k: int,
    flt: Filters,
    collection: str = store.CODE_COLLECTION,
) -> list[SearchHit]:
    vector = encoder.encode_query(query)
    if not vector.indices:
        return []
    response = client.query_points(
        collection_name=collection,
        query=models.SparseVector(indices=vector.indices, values=vector.values),
        using=store.SPARSE,
        limit=k,
        query_filter=flt.to_qdrant(),
        with_payload=True,
    )
    return _hits(response.points, Retriever.SPARSE, flt)


def ripgrep_search_chunks(
    client: QdrantClient,
    repo: Path,
    query: str,
    k: int,
    flt: Filters,
    collection: str = store.CODE_COLLECTION,
) -> list[SearchHit]:
    """Literal matches, resolved to the chunks that contain them.

    ripgrep returns file:line; retrieval ranks chunks. The bridge is a payload
    lookup per matched file — cheap at demo scale — that finds the chunk whose
    span covers each hit line. A chunk is ranked by how many matches it holds:
    the function with three call sites of the symbol beats the one with a passing
    mention.
    """
    if not ripgrep_available():
        return []
    matches = ripgrep_search(query, repo, fixed_string=True, word=True, max_count=200)
    if not matches:
        return []

    chunks_by_path = _chunks_for_paths(client, sorted({m.path for m in matches}), collection)
    counts: dict[str, int] = {}
    chosen: dict[str, Chunk] = {}
    for match in matches:
        for chunk in chunks_by_path.get(match.path, ()):
            if chunk.start_line <= match.line <= chunk.end_line:
                counts[chunk.chunk_id] = counts.get(chunk.chunk_id, 0) + 1
                chosen.setdefault(chunk.chunk_id, chunk)
                break

    ranked = sorted(counts, key=lambda cid: counts[cid], reverse=True)
    hits: list[SearchHit] = []
    for chunk_id in ranked:
        chunk = chosen[chunk_id]
        if not flt.keep(chunk):
            continue
        hits.append(
            SearchHit(
                chunk=chunk,
                score=float(counts[chunk_id]),
                retrievers=[Retriever.RIPGREP],
                component_scores={Retriever.RIPGREP.value: float(counts[chunk_id])},
            )
        )
        if len(hits) >= k:
            break
    return hits


def _chunks_for_paths(
    client: QdrantClient, paths: list[str], collection: str
) -> dict[str, list[Chunk]]:
    out: dict[str, list[Chunk]] = {}
    for path in paths:
        points, _ = client.scroll(
            collection_name=collection,
            scroll_filter=models.Filter(
                must=[models.FieldCondition(key="path", match=models.MatchValue(value=path))]
            ),
            with_payload=True,
            limit=2000,
        )
        out[path] = [store.chunk_from_payload(p.payload) for p in points if p.payload]
    return out


def rrf_fuse(lists: list[list[SearchHit]], k: int = RRF_K) -> list[SearchHit]:
    """Reciprocal Rank Fusion of ranked hit lists into one.

    Each list contributes ``1 / (k + rank)`` per chunk; a chunk found by several
    retrievers accumulates their contributions and its ``retrievers`` merge. The
    fused ``score`` replaces the per-retriever raw scores, which survive in
    ``component_scores`` for the trace.
    """
    fused: dict[str, SearchHit] = {}
    scores: dict[str, float] = {}
    for hit_list in lists:
        for rank, hit in enumerate(hit_list):
            cid = hit.chunk.chunk_id
            scores[cid] = scores.get(cid, 0.0) + 1.0 / (k + rank + 1)
            if cid not in fused:
                fused[cid] = SearchHit(chunk=hit.chunk, score=0.0)
            merged = fused[cid]
            for retriever in hit.retrievers:
                if retriever not in merged.retrievers:
                    merged.retrievers.append(retriever)
            merged.component_scores.update(hit.component_scores)
    for cid, hit in fused.items():
        hit.score = scores[cid]
    return sorted(fused.values(), key=lambda h: h.score, reverse=True)


SearchMode = Literal["auto", "dense", "sparse", "hybrid"]


def hybrid_search(
    query: str,
    *,
    k: int | None = None,
    filters: Filters | None = None,
    settings: Settings | None = None,
    client: QdrantClient | None = None,
    embedder: Embedder | None = None,
    encoder: BM25Encoder | None = None,
    repo: str | Path | None = None,
    collection: str = store.CODE_COLLECTION,
    mode: SearchMode = "auto",
) -> list[SearchHit]:
    """The retrieval entrypoint: pick retrievers by query shape, fuse, truncate.

    ``mode="auto"`` (the live default) routes by query shape: identifier-shaped →
    ripgrep + sparse (lexical, exact), otherwise dense + sparse (semantic +
    lexical). ``dense`` / ``sparse`` / ``hybrid`` force a fixed retriever set — the
    ablation uses these to isolate one variable per row (dense-only baselines, or
    hybrid regardless of the gate). Injectable dependencies keep the eval harness
    and the tests from standing up real models.
    """
    settings = settings or get_settings()
    k = k or settings.retrieval_top_k
    flt = filters or Filters()
    client = client or store.build_client(settings)

    if not client.collection_exists(collection):
        return []

    # Fetch deeper than k per list so fusion and post-filtering have material.
    fetch = max(k * 3, 30)
    encoder = encoder or load_encoder(settings, collection)

    def dense() -> list[SearchHit]:
        emb = embedder or build_embedder(settings)
        return dense_search(client, emb, query, fetch, flt, collection)

    def sparse() -> list[SearchHit]:
        return sparse_search(client, encoder, query, fetch, flt, collection)

    lists: list[list[SearchHit]] = []
    if mode == "dense":
        lists.append(dense())
    elif mode == "sparse":
        lists.append(sparse())
    elif mode == "hybrid":
        lists += [dense(), sparse()]
    elif is_identifier_query(query):  # mode == "auto"
        repo_path = Path(repo or settings.target_repo)
        lists.append(ripgrep_search_chunks(client, repo_path, query, fetch, flt, collection))
        lists.append(sparse())
    else:
        lists += [dense(), sparse()]

    fused = rrf_fuse(lists)
    if flt.prefer_implementation:
        fused = _demote_tests(fused)
    return fused[:k]


def _demote_tests(hits: list[SearchHit]) -> list[SearchHit]:
    """Stable-partition test chunks below implementation, preserving fused order.

    A demotion, not a filter: a test can still be retrieved (a query may genuinely
    be about one), but it never buries the implementation it exercises.
    """
    impl = [h for h in hits if not is_test_path(h.chunk.path)]
    tests = [h for h in hits if is_test_path(h.chunk.path)]
    return impl + tests
