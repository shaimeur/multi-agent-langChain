"""Lexical half of hybrid retrieval — cahier §6.5.

Dense embeddings are good at *"where is authentication handled?"* and bad at
*"find every call site of parse_config"*. Identifiers are arbitrary strings; their
meaning is that they match exactly. So the sparse side has to tokenize the way a
programmer reads code: `parse_config`, `parseConfig` and `ParseConfig` are the
same word, and each is also the two words that compose it.

BM25 weighting over that vocabulary, stored as Qdrant sparse vectors so the
lexical index is not a second store to keep in sync (ADR-002).
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

# Split on non-alphanumerics, then again at camelCase humps and digit runs.
_WORD = re.compile(r"[A-Za-z0-9_]+")
_CAMEL = re.compile(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|[a-z]+|\d+")

STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "of",
        "to",
        "in",
        "for",
        "on",
        "and",
        "or",
        "it",
        "this",
        "that",
        "with",
        "as",
        "by",
        "at",
        "from",
        "be",
        "self",
        "cls",
        "def",
        "class",
        "return",
        "import",
        "none",
        "true",
        "false",
    }
)

MIN_TOKEN_LEN = 2
K1 = 1.5
"""BM25 term-frequency saturation."""
B = 0.75
"""BM25 length normalisation."""


def tokenize(text: str) -> list[str]:
    """Code-aware tokens: the whole identifier plus its component words.

    Emitting both means `parse_config` is retrievable by the exact symbol *and*
    by the words "parse" and "config", without the caller choosing in advance
    which one the user will type.
    """
    tokens: list[str] = []
    for word in _WORD.findall(text):
        lowered = word.lower()
        if len(lowered) >= MIN_TOKEN_LEN and lowered not in STOPWORDS:
            tokens.append(lowered)
        parts = _CAMEL.findall(word)
        if len(parts) > 1:
            tokens.extend(
                part.lower()
                for part in parts
                if len(part) >= MIN_TOKEN_LEN and part.lower() not in STOPWORDS
            )
    return tokens


def term_id(token: str) -> int:
    """Stable non-negative 32-bit id for a token.

    Hashing rather than a persisted vocabulary means a reindex never has to
    migrate term ids, and a term unseen at index time still lines up at query
    time. Collisions are possible and harmless at this vocabulary size.
    """
    import hashlib

    return int.from_bytes(hashlib.blake2b(token.encode(), digest_size=4).digest(), "big")


@dataclass
class SparseVector:
    """Qdrant's sparse representation: parallel index/value arrays."""

    indices: list[int] = field(default_factory=list)
    values: list[float] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.indices)


@dataclass
class BM25Encoder:
    """Corpus statistics needed to weight a term. Fitted once per index build."""

    doc_freq: dict[int, int] = field(default_factory=dict)
    total_docs: int = 0
    avg_len: float = 0.0

    def fit(self, corpus: list[str]) -> BM25Encoder:
        lengths = []
        for text in corpus:
            tokens = tokenize(text)
            lengths.append(len(tokens))
            for tid in {term_id(t) for t in tokens}:
                self.doc_freq[tid] = self.doc_freq.get(tid, 0) + 1
        self.total_docs = len(corpus)
        self.avg_len = (sum(lengths) / len(lengths)) if lengths else 0.0
        return self

    def _idf(self, tid: int) -> float:
        """BM25 IDF, floored at zero so a term in every document cannot go negative."""
        if not self.total_docs:
            return 0.0
        df = self.doc_freq.get(tid, 0)
        return max(math.log(1 + (self.total_docs - df + 0.5) / (df + 0.5)), 0.0)

    def encode_document(self, text: str) -> SparseVector:
        tokens = tokenize(text)
        if not tokens:
            return SparseVector()
        counts = Counter(term_id(t) for t in tokens)
        length = len(tokens)
        norm = K1 * (1 - B + B * (length / self.avg_len)) if self.avg_len else K1

        vector = SparseVector()
        for tid, tf in counts.items():
            weight = self._idf(tid) * (tf * (K1 + 1)) / (tf + norm)
            if weight > 0:
                vector.indices.append(tid)
                vector.values.append(round(weight, 6))
        return vector

    def encode_query(self, text: str) -> SparseVector:
        """Queries carry no term frequency — presence is the whole signal."""
        vector = SparseVector()
        for tid in dict.fromkeys(term_id(t) for t in tokenize(text)):
            vector.indices.append(tid)
            vector.values.append(1.0)
        return vector

    def to_dict(self) -> dict:
        return {
            "doc_freq": {str(k): v for k, v in self.doc_freq.items()},
            "total_docs": self.total_docs,
            "avg_len": self.avg_len,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> BM25Encoder:
        return cls(
            doc_freq={int(k): v for k, v in payload.get("doc_freq", {}).items()},
            total_docs=payload.get("total_docs", 0),
            avg_len=payload.get("avg_len", 0.0),
        )
