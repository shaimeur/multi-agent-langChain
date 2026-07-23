"""Parent-document expansion and token-budget packing → ContextPack (cahier §6.5).

Match on the precise unit (the function or method chunk), then hand the generator
the enclosing section (the whole class, or both halves of one oversized def). This
is the parent-document idea from §6.2: retrieval wants a small, specific vector to
match on; generation wants the surrounding code that makes the match legible.

The "parent document" here is the *top-level definition* a chunk belongs to,
recovered from stored metadata without re-reading the repo: two chunks share a
parent when they share a file and a top-level symbol. A method ``Class.method``
rolls up to ``Class``; the two halves of a split ``class ReindentFilter`` share
the symbol ``ReindentFilter``; a module-level function is its own parent. So
expanding a retrieved method surfaces its siblings and the class header, and
expanding one half of a split definition surfaces the other — which is precisely
the recall the §13.1 "+ parent expansion" row exists to measure.

One ``group_key`` drives both the shipped packer and the ablation's coverage, so
the measured lift is the lift of the mechanism that ships — not an eval-only
approximation of it.
"""

from __future__ import annotations

from collections import defaultdict

from qdrant_client import QdrantClient

from forge.models import Chunk, ContextPack, SearchHit
from forge.rag import store

CHARS_PER_TOKEN = 4
"""Rough char→token ratio for budgeting. Deliberately dependency-free: a real
tokenizer would tie the packer to one model family, and the budget only has to be
the right order of magnitude to stop a pack overflowing the context window."""

DEFAULT_TOKEN_BUDGET = 6_000


def group_key(chunk: Chunk) -> tuple[str, str]:
    """``(path, top-level symbol)`` — the identity of a chunk's parent document.

    The top-level symbol is the first dotted component (``Class`` for
    ``Class.method``), the bare symbol for a top-level def, or ``<module>`` for
    the module-level leftover chunk that has no symbol.
    """
    top = chunk.symbol.split(".")[0] if chunk.symbol else "<module>"
    return (chunk.path, top)


def _token_estimate(text: str) -> int:
    return max(len(text) // CHARS_PER_TOKEN, 1)


def load_groups(
    client: QdrantClient, collection: str = store.CODE_COLLECTION
) -> dict[tuple[str, str], list[Chunk]]:
    """Map every parent-document key to its member chunks, ordered by line.

    One scroll of the whole collection — a few hundred chunks at demo scale — is
    cheaper and simpler than a per-hit query, and the map is reused across every
    query in an eval run.
    """
    groups: dict[tuple[str, str], list[Chunk]] = defaultdict(list)
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection, with_payload=True, limit=1024, offset=offset
        )
        for point in points:
            if point.payload:
                chunk = store.chunk_from_payload(point.payload)
                groups[group_key(chunk)].append(chunk)
        if offset is None:
            break
    for members in groups.values():
        members.sort(key=lambda c: c.start_line)
    return groups


def expand_hits(hits: list[SearchHit], groups: dict[tuple[str, str], list[Chunk]]) -> list[Chunk]:
    """Expand a ranked hit list into its parent documents, in place and deduped.

    A group is emitted whole at the rank of its highest-ranked member; later hits
    that fall in the same group add nothing. The result stays rank-ordered, so
    scoring its top-k is the honest "same slots, expanded content" comparison the
    ablation makes against the un-expanded row.
    """
    seen_groups: set[tuple[str, str]] = set()
    seen_ids: set[str] = set()
    out: list[Chunk] = []
    for hit in hits:
        key = group_key(hit.chunk)
        if key in seen_groups:
            continue
        seen_groups.add(key)
        for member in groups.get(key) or [hit.chunk]:
            if member.chunk_id not in seen_ids:
                seen_ids.add(member.chunk_id)
                out.append(member)
    return out


def pack_context(
    hits: list[SearchHit],
    *,
    groups: dict[tuple[str, str], list[Chunk]] | None = None,
    client: QdrantClient | None = None,
    collection: str = store.CODE_COLLECTION,
    token_budget: int = DEFAULT_TOKEN_BUDGET,
    expand: bool = True,
    queries: list[str] | None = None,
) -> ContextPack:
    """Ranked hits → an expanded, budget-bounded ContextPack for generation.

    Greedy in rank order: append until the next chunk would exceed the token
    budget, then stop — respecting rank rather than repacking by fit, because the
    generator reads the pack top-down and the best evidence should lead. The first
    chunk is always admitted, so a single oversized unit still produces a usable
    pack rather than an empty one.
    """
    if expand:
        if groups is None:
            if client is None:
                raise ValueError("pack_context(expand=True) needs `groups` or a `client`")
            groups = load_groups(client, collection)
        chunks = expand_hits(hits, groups)
    else:
        chunks = [h.chunk for h in hits]

    packed: list[Chunk] = []
    used = 0
    for chunk in chunks:
        cost = _token_estimate(chunk.raw)
        if packed and used + cost > token_budget:
            break
        packed.append(chunk)
        used += cost
    return ContextPack(chunks=packed, queries=queries or [])
