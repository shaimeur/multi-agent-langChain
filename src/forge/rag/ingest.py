"""Walk -> chunk -> embed -> upsert, full or incremental — cahier §6.1.

Incremental reindex is driven by `git diff --name-only` between the sha recorded
at the last index and HEAD. It is the default; a full walk is the fallback, not
the other way round.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from qdrant_client import QdrantClient

from forge.config import Settings, get_settings
from forge.models import Chunk
from forge.rag import store
from forge.rag.chunkers import chunk_file
from forge.rag.embed import Embedder, build_embedder
from forge.rag.sparse import BM25Encoder
from forge.rag.walker import SourceFile, changed_files, head_sha, walk_repo

ChunkFn = Callable[[SourceFile, str, str], list[Chunk]]

MANIFEST = "index-manifest.json"


@dataclass
class IndexReport:
    """What D2's definition of done asks to be recorded."""

    repo: str
    collection: str
    files: int = 0
    chunks: int = 0
    deleted: int = 0
    incremental: bool = False
    git_sha: str = ""
    embedder: str = ""
    seconds: float = 0.0
    skipped: list[str] = field(default_factory=list)

    def summary(self) -> str:
        mode = "incremental" if self.incremental else "full"
        return (
            f"{mode} index of {self.repo}: {self.chunks} chunks from {self.files} files "
            f"in {self.seconds:.1f}s ({self.embedder}, sha {self.git_sha or 'n/a'})"
        )


def _manifest_path(settings: Settings, collection: str) -> Path:
    return Path(settings.qdrant_path).parent / f"{collection}-{MANIFEST}"


def read_manifest(settings: Settings, collection: str) -> dict:
    path = _manifest_path(settings, collection)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_manifest(settings: Settings, collection: str, payload: dict) -> None:
    path = _manifest_path(settings, collection)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def collect_chunks(
    repo: Path, *, only: list[str] | None = None, chunk_fn: ChunkFn = chunk_file
) -> tuple[list[Chunk], int]:
    """Chunk every indexable file. Returns the chunks and the file count.

    ``chunk_fn`` defaults to the AST-aware chunker; the ablation passes
    ``chunk_naive`` to build its fixed-character-window baseline collection.
    """
    sha = head_sha(repo)
    name = repo.resolve().name
    chunks: list[Chunk] = []
    files = 0
    for source in walk_repo(repo, only=only):
        files += 1
        chunks.extend(chunk_fn(source, name, sha))
    return chunks, files


def index_repo(
    repo: Path,
    *,
    settings: Settings | None = None,
    client: QdrantClient | None = None,
    embedder: Embedder | None = None,
    collection: str = store.CODE_COLLECTION,
    full: bool = False,
    chunk_fn: ChunkFn = chunk_file,
) -> IndexReport:
    """Index ``repo`` into ``collection``, incrementally when possible."""
    settings = settings or get_settings()
    client = client or store.build_client(settings)
    embedder = embedder or build_embedder(settings)
    repo = Path(repo).resolve()

    started = time.perf_counter()
    sha = head_sha(repo)
    manifest = read_manifest(settings, collection)

    # An embedder change invalidates every stored vector, so it forces a rebuild.
    embedder_changed = manifest.get("embedder") not in (None, embedder.name)
    previous_sha = manifest.get("git_sha", "")

    # None means "walk everything"; [] means "incremental with nothing to do".
    # Collapsing those two would make an unchanged repo trigger a full rebuild.
    if full or embedder_changed or not previous_sha:
        touched = None
    elif previous_sha == sha:
        touched = []
    else:
        touched = changed_files(repo, previous_sha)
    incremental = touched is not None

    if incremental and not touched:
        # Nothing moved since the last index; do not pay to rebuild.
        return IndexReport(
            repo=str(repo),
            collection=collection,
            incremental=True,
            git_sha=sha,
            embedder=embedder.name,
            seconds=time.perf_counter() - started,
            chunks=store.count(client, collection),
        )

    chunks, files = collect_chunks(repo, only=touched, chunk_fn=chunk_fn)

    store.ensure_collection(
        client,
        collection,
        embedder.dim,
        recreate=embedder_changed or full,
        payload_indexes=bool(settings.qdrant_url),
    )

    deleted = 0
    if incremental and touched:
        store.delete_by_paths(client, collection, touched)
        deleted = len(touched)

    if chunks:
        # BM25 statistics come from the corpus being written. On an incremental
        # pass that is only the changed files, which skews IDF slightly; the
        # honest fix is a full reindex, and `forge index --full` is how you get it.
        encoder = BM25Encoder().fit([c.text for c in chunks])
        dense = embedder.embed_documents([c.text for c in chunks])
        points = [
            store.to_point(chunk, vector, encoder.encode_document(chunk.text))
            for chunk, vector in zip(chunks, dense, strict=True)
        ]
        store.upsert_chunks(client, collection, points)
        write_manifest(
            settings,
            collection,
            {
                "repo": str(repo),
                "git_sha": sha,
                "embedder": embedder.name,
                "dim": embedder.dim,
                "bm25": encoder.to_dict() if not incremental else manifest.get("bm25", {}),
            },
        )

    return IndexReport(
        repo=str(repo),
        collection=collection,
        files=files,
        chunks=len(chunks),
        deleted=deleted,
        incremental=incremental,
        git_sha=sha,
        embedder=embedder.name,
        seconds=time.perf_counter() - started,
    )
