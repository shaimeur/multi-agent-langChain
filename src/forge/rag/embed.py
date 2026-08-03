"""Dense embeddings behind an interface — cahier §6.3.

Model-agnostic on purpose: the ablation in §13.1 compares configurations, and a
hard-wired model would make that comparison a rewrite instead of a setting.

Two backends ship. The sentence-transformers one is what runs; the hashing one
exists so tests and CI never download 2 GB of weights to assert that a chunk
reached the right collection.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable

from forge.config import Settings, get_settings


@runtime_checkable
class Embedder(Protocol):
    """Anything that turns text into fixed-width dense vectors."""

    @property
    def dim(self) -> int: ...

    @property
    def name(self) -> str: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic bag-of-tokens vectors. No model, no download, no network.

    Not a serious retrieval model — related terms share no dimensions — but it is
    stable, fast and good enough to assert that plumbing works. Every test uses
    it; nothing in the live path does.
    """

    def __init__(self, dim: int = 256) -> None:
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def name(self) -> str:
        return f"hashing-{self._dim}"

    def _vector(self, text: str) -> list[float]:
        from forge.rag.sparse import tokenize

        vec = [0.0] * self._dim
        for token in tokenize(text):
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            vec[int.from_bytes(digest, "big") % self._dim] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        return [v / norm for v in vec] if norm else vec

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


class SentenceTransformerEmbedder:
    """The real backend. Weights load lazily — importing must stay cheap."""

    def __init__(self, model_name: str, *, batch_size: int = 16) -> None:
        self._model_name = model_name
        self._batch_size = batch_size
        self._model = None
        self._dim: int | None = None

    @property
    def name(self) -> str:
        return self._model_name

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            # CPU is not a fallback here, it is the only option — no NVIDIA
            # driver on this machine. Batch size is tuned for that, not for GPU.
            self._model = SentenceTransformer(self._model_name, device="cpu")
            # Renamed in sentence-transformers 5.x; both spellings are in the wild.
            getter = (
                getattr(self._model, "get_embedding_dimension", None)
                or self._model.get_sentence_embedding_dimension
            )
            self._dim = getter()
        return self._model

    @property
    def dim(self) -> int:
        self._load()
        assert self._dim is not None
        return self._dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        model = self._load()
        vectors = model.encode(
            texts,
            batch_size=self._batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]


def build_embedder(settings: Settings | None = None) -> Embedder:
    """The configured embedder. ``EMBEDDING_MODEL=hashing`` selects the stub."""
    settings = settings or get_settings()
    if settings.embedding_model == "hashing":
        return HashingEmbedder()
    return SentenceTransformerEmbedder(settings.embedding_model)
