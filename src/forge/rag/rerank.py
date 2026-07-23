"""Cross-encoder reranking — built for the eval harness, off in the live path.

descope §3. A cross-encoder scores each ``(query, chunk)`` pair *jointly* instead
of comparing two independently-embedded vectors, which is markedly more accurate
and markedly slower — hundreds of ms to seconds per query on this CPU-only
machine. So it is measured in the §13.1 ablation (row 4) and shipped disabled
behind ``RERANK_ENABLED``: the table then states exactly what turning it off buys
back in latency and costs in nDCG, which is a stronger claim than shipping it
silently or dropping it silently.

The live retrieval path never constructs a reranker. The harness does, explicitly.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from forge.config import Settings, get_settings
from forge.models import SearchHit


@runtime_checkable
class Reranker(Protocol):
    """Anything that reorders fused hits by joint query-document relevance."""

    def rerank(self, query: str, hits: list[SearchHit], *, top_k: int) -> list[SearchHit]: ...


def apply_scores(hits: list[SearchHit], scores: list[float], top_k: int) -> list[SearchHit]:
    """Reorder ``hits`` by ``scores`` (descending), keep ``top_k``.

    Split out from any model so the reranking *mechanism* — the sort and the
    truncation — is unit-testable without downloading a cross-encoder. The score
    lands in ``component_scores['rerank']`` for the trace and overwrites the fused
    ``score`` as the now-operative ranking signal.
    """
    for hit, score in zip(hits, scores, strict=True):
        hit.score = float(score)
        hit.component_scores["rerank"] = float(score)
    return sorted(hits, key=lambda h: h.score, reverse=True)[:top_k]


class CrossEncoderReranker:
    """The real backend: a sentence-transformers ``CrossEncoder`` on CPU.

    Weights load lazily and the model is injectable, so importing this module
    costs nothing and tests never touch the network.
    """

    def __init__(self, model_name: str, *, model: Any | None = None) -> None:
        self._model_name = model_name
        self._model = model

    @property
    def name(self) -> str:
        return self._model_name

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import CrossEncoder

            # CPU is the only option here; no NVIDIA driver on this machine.
            self._model = CrossEncoder(self._model_name, device="cpu")
        return self._model

    def rerank(self, query: str, hits: list[SearchHit], *, top_k: int) -> list[SearchHit]:
        if not hits:
            return []
        scores = self._load().predict([(query, hit.chunk.raw) for hit in hits])
        return apply_scores(list(hits), [float(s) for s in scores], top_k)


def build_reranker(settings: Settings | None = None) -> CrossEncoderReranker:
    """The configured cross-encoder. Harness-only — nothing in the live path calls this."""
    settings = settings or get_settings()
    return CrossEncoderReranker(settings.rerank_model)
