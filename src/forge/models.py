"""Schemas shared across retrieval, the graph and the API.

These are the contract between agents. Cahier §5.4 makes the point that typed
payloads, not prose, are what carry information between nodes — so anything that
crosses an agent boundary is defined here rather than passed as a dict.
"""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import BaseModel, Field


class ChunkKind(StrEnum):
    """What a chunk is, which decides how it gets enriched and expanded."""

    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    MODULE = "module"
    """Top-level code that belongs to no definition — imports, constants, main."""
    PROSE = "prose"
    """Markdown, docs, anything chunked by structure rather than syntax."""


class Chunk(BaseModel):
    """One retrievable unit, carrying everything a citation needs to resolve.

    ``text`` is what gets embedded — code plus the enrichment header. ``raw`` is
    what gets shown to a human and packed into a ContextPack. They differ on
    purpose: the header materially lifts recall but would be noise in a diff.
    """

    chunk_id: str
    repo: str
    path: str
    language: str
    kind: ChunkKind
    symbol: str | None = None
    """Qualified name, e.g. ``SessionManager.refresh``."""
    start_line: int
    """1-indexed and inclusive, matching what an editor shows."""
    end_line: int
    git_sha: str = ""
    parent_id: str | None = None
    """Enclosing chunk, for parent-document expansion (cahier §6.2)."""
    text: str
    raw: str

    @property
    def citation(self) -> str:
        """``src/auth/session.py:12-40`` — the form a user can click."""
        return f"{self.path}:{self.start_line}-{self.end_line}"


def make_chunk_id(repo: str, path: str, symbol: str | None, start_line: int) -> str:
    """Stable identity for a chunk across re-indexing runs.

    Deliberately excludes content and end_line: editing a function's body should
    update the chunk in place rather than orphan the old one and insert a new
    one. Line number is included because two same-named symbols can coexist in
    one file (a method and a module-level function, or a conditional import).
    """
    seed = f"{repo}\0{path}\0{symbol or ''}\0{start_line}"
    return hashlib.sha256(seed.encode()).hexdigest()[:16]


class Citation(BaseModel):
    """A claim's evidence. Verified programmatically at sentinel_out, never trusted."""

    chunk_id: str
    path: str
    start_line: int
    end_line: int


class ContextPack(BaseModel):
    """What the Retriever hands the Planner (cahier §4/A1)."""

    chunks: list[Chunk] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    """The rewritten queries actually issued, kept for the trace and evals."""

    def by_id(self, chunk_id: str) -> Chunk | None:
        return next((c for c in self.chunks if c.chunk_id == chunk_id), None)

    def supports(self, citation: Citation) -> bool:
        """True when the cited span exists in this pack.

        The groundedness check in cahier §8.4 is this function, not an LLM's
        opinion — which is the whole reason it lives in code.
        """
        chunk = self.by_id(citation.chunk_id)
        return (
            chunk is not None
            and chunk.path == citation.path
            and citation.start_line >= chunk.start_line
            and citation.end_line <= chunk.end_line
        )
