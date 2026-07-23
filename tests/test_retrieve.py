"""Hybrid retrieval against a real embedded Qdrant — HashingEmbedder, no network.

The embedder is the bag-of-tokens stub, so "semantic" here is lexical overlap;
that is enough to assert routing, fusion, filtering and the ripgrep→chunk bridge
all work. Retrieval *quality* is measured against sqlparse in evals/, not here.
"""

from __future__ import annotations

import subprocess

import pytest

from forge.config import CacheMode, Settings
from forge.models import Chunk, ChunkKind, Retriever, SearchHit
from forge.rag import store
from forge.rag.embed import HashingEmbedder
from forge.rag.ingest import index_repo
from forge.rag.retrieve import (
    Filters,
    dense_search,
    hybrid_search,
    is_identifier_query,
    load_encoder,
    ripgrep_search_chunks,
    rrf_fuse,
    sparse_search,
)
from forge.tools.ripgrep import ripgrep_available


@pytest.fixture
def target(tmp_path):
    repo = tmp_path / "target"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "tokenizer.py").write_text(
        "def tokenize(text):\n"
        '    """Split raw SQL into a list of tokens."""\n'
        "    return text.split()\n"
        "\n"
        "\n"
        "class Lexer:\n"
        '    """Turn a SQL string into a token stream."""\n'
        "\n"
        "    def scan(self, sql):\n"
        "        return tokenize(sql)\n"
    )
    (repo / "src" / "formatter.py").write_text(
        "def format_sql(statement):\n"
        '    """Pretty-print a parsed SQL statement as uppercase."""\n'
        "    return statement.upper()\n"
    )
    (repo / "README.md").write_text("# Demo\n\nA small demo repository for retrieval tests.\n")
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"],
        cwd=repo,
        check=True,
    )
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


@pytest.fixture
def indexed(target, settings, client):
    index_repo(target, settings=settings, client=client, embedder=HashingEmbedder(), full=True)
    return target


# --- the query gate (unit, no index) --------------------------------------


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("parse_config", True),
        ("SessionManager", True),
        ("SessionManager.refresh", True),
        ("Lexer::scan", True),
        ("format_sql", True),
        ("where is sql tokenized", False),
        ("tokenize", False),  # a bare lowercase word is a concept, not a symbol
        ("authentication", False),
        ("", False),
    ],
)
def test_identifier_query_gate(query, expected):
    assert is_identifier_query(query) is expected


# --- routing --------------------------------------------------------------


def test_semantic_query_finds_the_function(indexed, settings, client):
    hits = hybrid_search(
        "split a sql string into tokens", settings=settings, client=client, repo=indexed
    )

    assert hits
    assert hits[0].chunk.path == "src/tokenizer.py"
    assert Retriever.DENSE in hits[0].retrievers


@pytest.mark.skipif(not ripgrep_available(), reason="ripgrep (rg) not installed")
def test_identifier_query_takes_the_lexical_path(indexed, settings, client):
    hits = hybrid_search("format_sql", settings=settings, client=client, repo=indexed)

    symbols = {h.chunk.symbol for h in hits}
    assert "format_sql" in symbols
    # The lexical path runs ripgrep + sparse only — dense must not appear.
    assert all(Retriever.DENSE not in h.retrievers for h in hits)
    assert any(Retriever.RIPGREP in h.retrievers for h in hits)


# --- the individual retrievers --------------------------------------------


def test_dense_search_returns_relevant_chunks(indexed, settings, client):
    hits = dense_search(client, HashingEmbedder(), "sql token stream", k=5, flt=Filters())

    assert hits
    assert all(h.retrievers == [Retriever.DENSE] for h in hits)
    assert "src/tokenizer.py" in {h.chunk.path for h in hits}


def test_sparse_search_matches_the_exact_identifier(indexed, settings, client):
    encoder = load_encoder(settings)
    hits = sparse_search(client, encoder, "format_sql", k=5, flt=Filters())

    assert "format_sql" in {h.chunk.symbol for h in hits}
    assert all(h.retrievers == [Retriever.SPARSE] for h in hits)


@pytest.mark.skipif(not ripgrep_available(), reason="ripgrep (rg) not installed")
def test_ripgrep_search_chunks_maps_lines_to_chunks(indexed, settings, client):
    hits = ripgrep_search_chunks(client, indexed, "tokenize", k=5, flt=Filters())

    assert hits
    assert all(h.retrievers == [Retriever.RIPGREP] for h in hits)
    # The def and its call site both live in tokenizer.py.
    assert "src/tokenizer.py" in {h.chunk.path for h in hits}


# --- fusion (unit) --------------------------------------------------------


def _hit(chunk_id, retriever, score=1.0):
    chunk = Chunk(
        chunk_id=chunk_id,
        repo="r",
        path=f"{chunk_id}.py",
        language="python",
        kind=ChunkKind.FUNCTION,
        start_line=1,
        end_line=2,
        text="x",
        raw="x",
    )
    return SearchHit(
        chunk=chunk, score=score, retrievers=[retriever], component_scores={retriever.value: score}
    )


def test_rrf_merges_provenance_of_a_shared_chunk():
    dense = [_hit("a", Retriever.DENSE), _hit("b", Retriever.DENSE)]
    sparse = [_hit("b", Retriever.SPARSE), _hit("c", Retriever.SPARSE)]

    fused = rrf_fuse([dense, sparse])
    by_id = {h.chunk.chunk_id: h for h in fused}

    # 'b' is ranked by both, so it wins and carries both retrievers.
    assert fused[0].chunk.chunk_id == "b"
    assert set(by_id["b"].retrievers) == {Retriever.DENSE, Retriever.SPARSE}
    assert by_id["b"].component_scores.keys() == {"dense", "sparse"}


def test_rrf_orders_by_summed_reciprocal_rank():
    list_one = [_hit("x", Retriever.DENSE), _hit("y", Retriever.DENSE)]
    list_two = [_hit("y", Retriever.SPARSE), _hit("x", Retriever.SPARSE)]

    fused = rrf_fuse([list_one, list_two])
    # x: 1/61 + 1/62 ; y: 1/62 + 1/61 — a tie, but both beat any single-list chunk.
    assert {h.chunk.chunk_id for h in fused} == {"x", "y"}
    assert all(len(h.retrievers) == 2 for h in fused)


# --- filters --------------------------------------------------------------


def test_language_filter_restricts_to_one_language(indexed, settings, client):
    hits = hybrid_search(
        "demo repository",
        settings=settings,
        client=client,
        repo=indexed,
        filters=Filters(language="markdown"),
    )

    assert hits
    assert all(h.chunk.language == "markdown" for h in hits)


def test_path_prefix_filter_narrows_to_a_subtree(indexed, settings, client):
    hits = hybrid_search(
        "format a sql statement",
        settings=settings,
        client=client,
        repo=indexed,
        filters=Filters(path_prefix="src/formatter"),
    )

    assert hits
    assert all(h.chunk.path.startswith("src/formatter") for h in hits)


# --- degenerate cases -----------------------------------------------------


def test_search_on_an_unindexed_collection_is_empty(settings, client):
    assert hybrid_search("anything at all", settings=settings, client=client) == []
