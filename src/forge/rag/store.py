"""Qdrant collection management — cahier §6.4, ADR-002.

Dense and sparse vectors live in one collection under named vectors, so the
lexical index is not a second store to keep in sync. Payload carries everything a
citation needs, which is what lets `sentinel_out` verify a claim without going
back to disk.
"""

from __future__ import annotations

from pathlib import Path

from qdrant_client import QdrantClient, models

from forge.config import Settings, get_settings
from forge.models import Chunk, ChunkKind
from forge.rag.sparse import SparseVector

DENSE = "dense"
SPARSE = "sparse"
CODE_COLLECTION = "code"


def point_id(chunk_id: str) -> int:
    """Qdrant needs an unsigned int or UUID; chunk ids are 16 hex chars.

    Reusing the chunk id rather than a counter is what makes an upsert idempotent
    — reindexing the same symbol overwrites its point instead of duplicating it.
    """
    return int(chunk_id, 16)


def build_client(settings: Settings | None = None) -> QdrantClient:
    """Server mode when QDRANT_URL is set, embedded otherwise (ADR-002)."""
    settings = settings or get_settings()
    if settings.qdrant_url:
        return QdrantClient(url=settings.qdrant_url, timeout=30)
    path = Path(settings.qdrant_path)
    path.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(path))


def ensure_collection(
    client: QdrantClient,
    name: str,
    dim: int,
    *,
    recreate: bool = False,
    payload_indexes: bool = True,
) -> None:
    """Create the collection if absent. ``recreate`` drops it first.

    Recreation is the right default when the embedding model changes: vectors
    from two models in one collection produce silently meaningless neighbours.
    """
    exists = client.collection_exists(name)
    if exists and recreate:
        client.delete_collection(name)
        exists = False
    if exists:
        return

    client.create_collection(
        collection_name=name,
        vectors_config={DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE)},
        sparse_vectors_config={SPARSE: models.SparseVectorParams()},
    )
    # Payload indexes make the metadata filters in §6.5 cheap rather than a scan.
    # Embedded Qdrant ignores them and warns, so they are server-mode only.
    if not payload_indexes:
        return
    for field, schema in (
        ("path", models.PayloadSchemaType.KEYWORD),
        ("language", models.PayloadSchemaType.KEYWORD),
        ("kind", models.PayloadSchemaType.KEYWORD),
        ("symbol", models.PayloadSchemaType.KEYWORD),
    ):
        client.create_payload_index(name, field_name=field, field_schema=schema)


def to_point(chunk: Chunk, dense: list[float], sparse: SparseVector) -> models.PointStruct:
    return models.PointStruct(
        id=point_id(chunk.chunk_id),
        vector={
            DENSE: dense,
            SPARSE: models.SparseVector(indices=sparse.indices, values=sparse.values),
        },
        payload={
            "chunk_id": chunk.chunk_id,
            "repo": chunk.repo,
            "path": chunk.path,
            "language": chunk.language,
            "kind": chunk.kind.value,
            "symbol": chunk.symbol,
            "start_line": chunk.start_line,
            "end_line": chunk.end_line,
            "git_sha": chunk.git_sha,
            "parent_id": chunk.parent_id,
            "raw": chunk.raw,
        },
    )


def chunk_from_payload(payload: dict) -> Chunk:
    """Rebuild a Chunk from a search hit.

    ``text`` is reconstructed as ``raw`` because the enrichment header exists to
    be embedded, not to be read back — carrying it in the payload would inflate
    every stored point for no retrieval benefit.
    """
    return Chunk(
        chunk_id=payload["chunk_id"],
        repo=payload.get("repo", ""),
        path=payload["path"],
        language=payload.get("language", ""),
        kind=ChunkKind(payload.get("kind", ChunkKind.MODULE)),
        symbol=payload.get("symbol"),
        start_line=payload["start_line"],
        end_line=payload["end_line"],
        git_sha=payload.get("git_sha", ""),
        parent_id=payload.get("parent_id"),
        text=payload.get("raw", ""),
        raw=payload.get("raw", ""),
    )


def upsert_chunks(client: QdrantClient, name: str, points: list[models.PointStruct]) -> None:
    if points:
        client.upsert(collection_name=name, points=points, wait=True)


def delete_by_paths(client: QdrantClient, name: str, paths: list[str]) -> None:
    """Drop every chunk from these files.

    An incremental reindex has to delete before it writes: a function removed
    from a file would otherwise stay retrievable forever, and citing deleted code
    is exactly the failure mode grounding is supposed to prevent.
    """
    if not paths:
        return
    client.delete(
        collection_name=name,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[models.FieldCondition(key="path", match=models.MatchAny(any=paths))]
            )
        ),
        wait=True,
    )


def count(client: QdrantClient, name: str) -> int:
    if not client.collection_exists(name):
        return 0
    return client.count(collection_name=name, exact=True).count
