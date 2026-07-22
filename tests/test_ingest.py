"""End-to-end ingestion against a real embedded Qdrant — no network, no weights."""

from __future__ import annotations

import subprocess

import pytest

from forge.config import CacheMode, Settings
from forge.rag import store
from forge.rag.embed import HashingEmbedder
from forge.rag.ingest import index_repo, read_manifest


def _commit(repo, message):
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", message],
        cwd=repo,
        check=True,
    )


@pytest.fixture
def target(tmp_path):
    repo = tmp_path / "target"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "auth.py").write_text(
        "def parse_config(path):\n"
        '    """Read config."""\n'
        "    return {}\n\n\n"
        "class SessionManager:\n"
        "    def refresh(self):\n"
        "        return True\n"
    )
    (repo / "README.md").write_text("# Target\n\nA demo repo.\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    _commit(repo, "init")
    return repo


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        cache_mode=CacheMode.REPLAY,
        embedding_model="hashing",
        qdrant_url="",
        qdrant_path=tmp_path / "qdrant",
    )


@pytest.fixture
def client(settings):
    c = store.build_client(settings)
    yield c
    c.close()


def _index(target, settings, client, **kw):
    return index_repo(target, settings=settings, client=client, embedder=HashingEmbedder(), **kw)


def test_full_index_writes_every_chunk(target, settings, client):
    report = _index(target, settings, client)

    assert report.files == 2
    assert report.chunks > 0
    assert not report.incremental
    assert store.count(client, store.CODE_COLLECTION) == report.chunks


def test_symbols_survive_the_round_trip(target, settings, client):
    _index(target, settings, client)

    points, _ = client.scroll(store.CODE_COLLECTION, limit=100, with_payload=True)
    symbols = {p.payload["symbol"] for p in points}

    assert {"parse_config", "SessionManager", "SessionManager.refresh"} <= symbols


def test_payload_carries_what_a_citation_needs(target, settings, client):
    _index(target, settings, client)

    points, _ = client.scroll(store.CODE_COLLECTION, limit=100, with_payload=True)
    payload = next(p.payload for p in points if p.payload["symbol"] == "parse_config")
    chunk = store.chunk_from_payload(payload)

    assert chunk.path == "src/auth.py"
    assert chunk.start_line >= 1
    assert chunk.git_sha
    assert "def parse_config" in chunk.raw


def test_reindexing_is_idempotent(target, settings, client):
    """Point ids derive from chunk ids, so a rerun overwrites instead of duplicating."""
    first = _index(target, settings, client)
    _index(target, settings, client, full=True)

    assert store.count(client, store.CODE_COLLECTION) == first.chunks


def test_incremental_reindex_only_touches_changed_files(target, settings, client):
    _index(target, settings, client)

    (target / "src" / "auth.py").write_text(
        'def parse_config(path):\n    return {"changed": True}\n'
    )
    _commit(target, "edit")
    report = _index(target, settings, client)

    assert report.incremental
    assert report.files == 1, "unchanged README must not be rewalked"


def test_incremental_reindex_drops_deleted_symbols(target, settings, client):
    """Citing code that no longer exists is the failure grounding must prevent."""
    _index(target, settings, client)

    (target / "src" / "auth.py").write_text("def parse_config(path):\n    return {}\n")
    _commit(target, "remove SessionManager")
    _index(target, settings, client)

    points, _ = client.scroll(store.CODE_COLLECTION, limit=100, with_payload=True)
    symbols = {p.payload["symbol"] for p in points}

    assert "SessionManager.refresh" not in symbols
    assert "parse_config" in symbols


def test_no_changes_means_no_rebuild(target, settings, client):
    first = _index(target, settings, client)
    second = _index(target, settings, client)

    assert second.incremental
    assert second.chunks == first.chunks
    assert second.files == 0, "an unchanged repo must not be rewalked"


def test_changing_the_embedder_forces_a_rebuild(target, settings, client):
    """Vectors from two models in one collection make neighbours meaningless."""
    _index(target, settings, client)
    assert read_manifest(settings, store.CODE_COLLECTION)["embedder"] == "hashing-256"

    report = index_repo(target, settings=settings, client=client, embedder=HashingEmbedder(dim=64))

    assert not report.incremental, "a new embedder must not reindex incrementally"
    assert read_manifest(settings, store.CODE_COLLECTION)["dim"] == 64


def test_manifest_records_what_was_indexed(target, settings, client):
    _index(target, settings, client)
    manifest = read_manifest(settings, store.CODE_COLLECTION)

    assert manifest["git_sha"]
    assert manifest["bm25"]["total_docs"] > 0
