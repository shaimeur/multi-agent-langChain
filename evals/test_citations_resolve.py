"""C3 — every citation in a grounded answer resolves to a real ``file:line`` span.

The full RAG pipeline proof (cahier §16, C3): index a repo, ask a question, and
check that each ``[n]`` the answer emits maps to a chunk whose ``(path, start_line,
end_line)`` is a real, openable location in that repo — not a number the model
invented. The model is a ``FakeListChatModel`` returning a fixed citation, so this
asserts the *resolution* contract (ingestion → retrieval → grounded generation →
verifiable file:line) with no network and no weights.

Run explicitly — ``testpaths`` is ``tests`` only:
    uv run pytest evals/test_citations_resolve.py
"""

from __future__ import annotations

import subprocess

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from forge.config import CacheMode, Settings
from forge.rag import store
from forge.rag.answer import answer_question
from forge.rag.embed import HashingEmbedder
from forge.rag.ingest import index_repo


@pytest.fixture
def target(tmp_path):
    """A tiny real repo: one Python file whose function is the only citable code."""
    repo = tmp_path / "target"
    (repo / "sqlparse").mkdir(parents=True)
    (repo / "sqlparse" / "utils.py").write_text(
        "def remove_quotes(val):\n"
        '    """Helper that removes surrounding quotes from strings."""\n'
        "    if val is None:\n"
        "        return\n"
        "    if val[0] in ('\"', \"'\", '`') and val[0] == val[-1]:\n"
        "        val = val[1:-1]\n"
        "    return val\n"
    )
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
    """Isolated from the developer's .env and real index — offline, hashing embedder."""
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


def test_every_citation_resolves_to_a_real_file_line(indexed, target, settings, client):
    """A grounded answer's citations each open onto a real span containing the code."""
    llm = FakeListChatModel(responses=["Quote stripping lives in remove_quotes [1]."])
    result = answer_question(
        "where are surrounding quotes removed",
        settings=settings,
        client=client,
        embedder=HashingEmbedder(),
        llm=llm,
    )

    assert result.grounded is True
    assert result.citations, "a [1] citation must resolve to a retrieved snippet"

    retrieved_ids = {s.chunk_id for s in result.sources}
    for c in result.citations:
        # 1) points at a snippet that was actually retrieved, not invented
        assert c.chunk_id in retrieved_ids, f"citation {c.chunk_id} was never retrieved"
        # 2) a sane, non-empty line span
        assert 1 <= c.start_line <= c.end_line, f"bad span {c.start_line}-{c.end_line}"
        # 3) resolves to a real, openable location on disk
        file = target / c.path
        assert file.is_file(), f"citation path does not exist: {c.path}"
        lines = file.read_text(encoding="utf-8").splitlines()
        assert c.end_line <= len(lines), f"citation overruns {c.path}: {c.end_line} > {len(lines)}"
        span = "\n".join(lines[c.start_line - 1 : c.end_line])
        assert span.strip(), "the cited span is empty on disk"
        assert "remove_quotes" in span, "the cited span does not contain the cited code"


def test_an_invented_citation_does_not_resolve(indexed, settings, client):
    """A `[n]` for a snippet the model was never shown resolves to nothing (§8.4)."""
    llm = FakeListChatModel(responses=["It is over in [99], trust me."])
    result = answer_question(
        "where are surrounding quotes removed",
        settings=settings,
        client=client,
        embedder=HashingEmbedder(),
        llm=llm,
    )
    assert result.citations == []
    assert result.grounded is False
