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


class Retriever(StrEnum):
    """Which retriever surfaced a hit.

    Kept on every ``SearchHit`` so the fused ranking can show *why* a chunk is
    there, and so D4's ablation can switch one off and measure what it cost.
    """

    DENSE = "dense"
    """Semantic similarity over the MiniLM/BGE vectors — 'where is X handled'."""
    SPARSE = "sparse"
    """BM25 over code-aware tokens — the exact-identifier half."""
    RIPGREP = "ripgrep"
    """Literal text match, resolved back to the chunk that contains the line."""


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


class SearchHit(BaseModel):
    """One retrieved chunk with its score and provenance (cahier §6.5).

    ``retrievers`` is a list, not a single value, because RRF fusion collapses a
    chunk that several retrievers ranked into one hit — and "dense *and* sparse
    both surfaced this" is exactly the signal the search table exists to show. A
    hit carries one retriever before fusion and can carry several after.
    ``component_scores`` keeps each retriever's raw score for the trace and the
    eval harness; ``score`` is the operative one — a raw similarity for a
    single-retriever hit, the fused RRF score once retrievers are combined.
    """

    chunk: Chunk
    score: float
    retrievers: list[Retriever] = Field(default_factory=list)
    component_scores: dict[str, float] = Field(default_factory=dict)

    @property
    def provenance(self) -> str:
        """``dense+sparse`` — the retrievers that found this, for the table."""
        return "+".join(r.value for r in self.retrievers)


class SourceRef(BaseModel):
    """A retrieved snippet offered to the answerer, in rank order.

    What the UI shows as "sources" and what a citation points into. Lighter than a
    full ``Chunk`` — it carries only what a human needs to open the file.
    """

    chunk_id: str
    path: str
    start_line: int
    end_line: int
    symbol: str | None = None

    @property
    def citation(self) -> str:
        return f"{self.path}:{self.start_line}-{self.end_line}"


class GroundedAnswer(BaseModel):
    """The output of the grounded-Q&A path (cahier §6.6).

    An answer whose every citation has been checked *in code* against the snippets
    that were actually retrieved (`ContextPack.supports`, §8.4) — never on the
    model's say-so. ``grounded`` is False when the model cited nothing verifiable,
    which is the honest signal that the answer is unsupported rather than a silent
    hallucination.
    """

    question: str
    answer: str
    grounded: bool = False
    citations: list[Citation] = Field(default_factory=list)
    sources: list[SourceRef] = Field(default_factory=list)


class Route(StrEnum):
    """Where the SUPERVISOR sends the turn next (cahier §4)."""

    RETRIEVE = "retrieve"
    """The question needs fresh code context — go to the RETRIEVER."""
    ANSWER = "answer"
    """Enough context is already in the pack — answer directly (a follow-up)."""
    END = "end"
    """A greeting, thanks, or nothing to do — no retrieval, no generation."""


class RouteDecision(BaseModel):
    """The SUPERVISOR's routing verdict — router tier, structured, never free text.

    Cahier §4 makes the supervisor a router, not a content generator: it emits this
    small typed decision and nothing else. ``rationale`` is one line kept for the
    trace, not shown to the user.
    """

    route: Route = Route.RETRIEVE
    rationale: str = ""


# --- planning and editing (cahier §4/A2, A3; §5.1) ------------------------


class CitationRef(BaseModel):
    """A plan step's evidence: the retrieved chunk it relies on.

    The PLANNER must cite at least one per step, and each must resolve into the
    ContextPack — a step that cites nothing, or cites a chunk that was never
    retrieved, is rejected *before any code is written* (cahier §4/A2). This is the
    same groundedness discipline as a citation, applied to a plan.
    """

    chunk_id: str
    why: str = ""
    """One line: what this chunk establishes for the step."""


class PlanStep(BaseModel):
    """One atomic change to one file, with the evidence that justifies it."""

    intent: str
    """What to change, in a sentence."""
    target_path: str
    """The repo-relative file this step edits."""
    evidence: list[CitationRef] = Field(default_factory=list)
    rationale: str = ""

    def is_grounded(self, pack: ContextPack) -> bool:
        """True when the step cites evidence and every citation is in ``pack``."""
        return bool(self.evidence) and all(
            pack.by_id(ref.chunk_id) is not None for ref in self.evidence
        )


class ChangePlan(BaseModel):
    """The PLANNER's output: an ordered, citation-backed plan (cahier §4/A2).

    ``needs_more_context`` lets the planner send the turn back to the RETRIEVER
    instead of guessing, with ``missing`` describing what to look for — the
    ``needs_more_context`` loop of §5.1, capped so it cannot ping-pong.
    """

    summary: str = ""
    steps: list[PlanStep] = Field(default_factory=list)
    needs_more_context: bool = False
    missing: str = ""

    def ungrounded_steps(self, pack: ContextPack) -> list[PlanStep]:
        """The steps that must be rejected — no evidence, or evidence not in the pack."""
        return [step for step in self.steps if not step.is_grounded(pack)]


class Patch(BaseModel):
    """One structured edit to one file — a search/replace, not raw diff syntax.

    The EDITOR emits these because asking a model (a weak local one especially) for
    valid unified-diff text is brittle; the diff is *built* deterministically from
    the edits (``tools/patch.py``) and only then checked with ``git apply --check``.
    ``old_string`` must occur exactly once in the target file so the edit is
    unambiguous.
    """

    path: str
    old_string: str
    new_string: str
    rationale: str = ""


class PatchSet(BaseModel):
    """The EDITOR's output for a plan step (cahier §4/A3).

    Structured edits the editor never writes to disk itself — ``tools/patch.py``
    turns them into a diff and dry-runs ``git apply --check`` in the session
    worktree; a patch that fails the check never reaches a human.
    """

    summary: str = ""
    patches: list[Patch] = Field(default_factory=list)
