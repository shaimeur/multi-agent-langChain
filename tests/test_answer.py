"""Grounded answering — citations are verified in code, never on the model's word.

A fake chat model stands in for the LLM, so these assert the grounding contract
(numbered snippets in, `[n]` citations out, verified against the pack) with no
network and no weights.
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
    repo = tmp_path / "target"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "tokenizer.py").write_text(
        "def tokenize(text):\n"
        '    """Split raw SQL into a list of tokens."""\n'
        "    return text.split()\n"
    )
    (repo / "README.md").write_text("# Demo\n\nA small demo repository.\n")
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


def _ask(question, settings, client, llm):
    return answer_question(
        question, settings=settings, client=client, embedder=HashingEmbedder(), llm=llm
    )


def test_a_valid_citation_grounds_the_answer(indexed, settings, client):
    llm = FakeListChatModel(responses=["Tokenizing is handled in the tokenizer [1]."])
    result = _ask("how is sql tokenized into a list", settings, client, llm)

    assert result.grounded is True
    assert result.citations, "a [1] citation must resolve"
    # [1] maps to the first retrieved snippet.
    assert result.citations[0].chunk_id == result.sources[0].chunk_id


def test_an_out_of_range_citation_is_dropped_and_ungrounds(indexed, settings, client):
    """The model cannot cite a snippet it was never shown."""
    llm = FakeListChatModel(responses=["It is over in [99], trust me."])
    result = _ask("how is sql tokenized into a list", settings, client, llm)

    assert result.citations == []
    assert result.grounded is False


def test_no_citation_means_ungrounded(indexed, settings, client):
    llm = FakeListChatModel(responses=["I cannot tell from the given context."])
    result = _ask("how is sql tokenized into a list", settings, client, llm)

    assert result.citations == []
    assert result.grounded is False


def test_empty_index_short_circuits_before_the_model(settings, client):
    """Nothing indexed: answer honestly, do not invent, do not even call the LLM."""
    llm = FakeListChatModel(responses=["should never be returned [1]"])
    result = _ask("anything at all", settings, client, llm)

    assert result.grounded is False
    assert result.citations == []
    assert "index" in result.answer.lower()
    assert result.sources == []
