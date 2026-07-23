"""Reranking mechanism — the sort and truncation, plus a fake cross-encoder.

The real model is never loaded here: ``apply_scores`` is pure, and
``CrossEncoderReranker`` takes an injected model, so the test asserts the wiring
without downloading weights.
"""

from __future__ import annotations

from forge.models import Chunk, ChunkKind, SearchHit
from forge.rag.rerank import CrossEncoderReranker, apply_scores


def _hit(cid: str, raw: str, score: float = 0.0) -> SearchHit:
    chunk = Chunk(
        chunk_id=cid,
        repo="r",
        path=f"{cid}.py",
        language="python",
        kind=ChunkKind.FUNCTION,
        start_line=1,
        end_line=1,
        text=raw,
        raw=raw,
    )
    return SearchHit(chunk=chunk, score=score)


def test_apply_scores_reorders_and_truncates():
    hits = [_hit("a", "a"), _hit("b", "b"), _hit("c", "c")]
    out = apply_scores(hits, [0.1, 0.9, 0.5], top_k=2)
    assert [h.chunk.chunk_id for h in out] == ["b", "c"]
    assert out[0].score == 0.9
    assert out[0].component_scores["rerank"] == 0.9


class _FakeCrossEncoder:
    """Scores a (query, doc) pair by how many query words the doc contains."""

    def predict(self, pairs):
        return [float(sum(w in doc for w in query.split())) for query, doc in pairs]


def test_cross_encoder_reranker_uses_the_model_scores():
    reranker = CrossEncoderReranker("unused/model", model=_FakeCrossEncoder())
    hits = [
        _hit("miss", "nothing relevant here"),
        _hit("hit", "parse the sql tokens"),
    ]
    out = reranker.rerank("sql tokens", hits, top_k=2)
    assert out[0].chunk.chunk_id == "hit"


def test_reranker_on_empty_hits_is_empty_and_loads_nothing():
    reranker = CrossEncoderReranker("unused/model")  # no model injected
    assert reranker.rerank("q", [], top_k=5) == []  # returns before touching the model
