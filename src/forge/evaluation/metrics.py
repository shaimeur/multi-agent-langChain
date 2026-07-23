"""Retrieval metrics keyed on (path, line-span) overlap, not exact chunk_id.

Why span overlap and not chunk_id equality. The §13.1 ablation compares chunking
*strategies* — naive fixed-character windows against AST boundaries — and those
produce entirely different chunk_ids for the same lines of code. An exact-id
metric cannot score the naive row at all. Overlap in (file, line) space is the
honest common denominator: *did the retriever surface the lines that answer the
question*, which is also exactly what a grounded citation needs to resolve.

The D3 baseline used the stricter exact-id match (Recall@10 = 0.400). Every id hit
is also a span hit, so the span-overlap number is that number or higher — the two
are related measurements of the same retriever, not rivals, and evaluation.md
reports both.

A golden answer is a *set* of relevant spans: one answer can legitimately span
several chunks — a class split in two by the size cap, a feature spread across a
couple of functions. A retrieved chunk *overlaps* a relevant span when they share
a file and their line ranges intersect. Every metric below is defined on that one
relation, so a change to how chunks are cut never changes what "correct" means.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

DEFAULT_KS = (5, 10)


@dataclass(frozen=True)
class Span:
    """A stretch of one file, 1-indexed and inclusive — a relevant answer or a
    retrieved chunk, measured in the same space so the two can be compared."""

    path: str
    start: int
    end: int

    def overlaps(self, other: Span) -> bool:
        """True when both touch the same file and their line ranges intersect."""
        return self.path == other.path and self.start <= other.end and other.start <= self.end


def _first_overlap_rank(relevant: Span, retrieved: list[Span]) -> int | None:
    """1-indexed rank of the earliest retrieved span that overlaps ``relevant``."""
    for rank, got in enumerate(retrieved, start=1):
        if got.overlaps(relevant):
            return rank
    return None


def score_query(
    retrieved: list[Span], relevant: list[Span], *, ks: tuple[int, ...] = DEFAULT_KS
) -> dict[str, float]:
    """Score one ranked retrieval against one query's set of relevant spans.

    ``retrieved`` is in rank order (best first). Returns Recall@k / Hit@k /
    Precision@k / nDCG@k for each k in ``ks``, plus ``rr`` (this query's
    contribution to MRR) and ``n_relevant``.

    nDCG uses *novel-coverage* binary gains: a retrieved chunk earns a gain of 1
    at its rank only if it is the first to overlap some as-yet-uncovered relevant
    span. That is what makes parent-document expansion legible to the metric — one
    expanded chunk that pulls in a class's second half covers a second relevant
    span and is credited for it — without letting three chunks that all overlap
    the same span inflate the score.
    """
    n_rel = len(relevant)
    first_rank = [_first_overlap_rank(rel, retrieved) for rel in relevant]

    # Per retrieved position: does it introduce coverage of a relevant span that
    # no higher-ranked chunk already covered?
    covered = [False] * n_rel
    novel = [False] * len(retrieved)
    for i, got in enumerate(retrieved):
        introduced = False
        for j, rel in enumerate(relevant):
            if not covered[j] and got.overlaps(rel):
                covered[j] = True
                introduced = True
        novel[i] = introduced

    out: dict[str, float] = {"n_relevant": float(n_rel)}
    for k in ks:
        found = sum(1 for fr in first_rank if fr is not None and fr <= k)
        out[f"recall@{k}"] = found / n_rel if n_rel else 0.0
        out[f"hit@{k}"] = 1.0 if found else 0.0

        top_k = retrieved[:k]
        rel_retrieved = sum(1 for got in top_k if any(got.overlaps(rel) for rel in relevant))
        out[f"precision@{k}"] = rel_retrieved / k if k else 0.0

        dcg = sum(1.0 / math.log2(i + 2) for i in range(min(k, len(retrieved))) if novel[i])
        idcg = sum(1.0 / math.log2(i + 2) for i in range(min(k, n_rel)))
        out[f"ndcg@{k}"] = dcg / idcg if idcg else 0.0

    ranks = [fr for fr in first_rank if fr is not None]
    out["rr"] = 1.0 / min(ranks) if ranks else 0.0
    return out


def coverage_recall_hit(
    groups_per_rank: list[list[Span]], relevant: list[Span], k: int
) -> tuple[float, float]:
    """Recall/hit when each retrieved rank contributes its whole parent document.

    ``groups_per_rank[i]`` is the spans of the group behind the i-th ranked hit —
    or just that hit's own span when expansion is off, which makes this identical
    to the plain Recall@k. This is how the "+ parent expansion" row is scored:
    parent-document retrieval hands the generator each matched chunk's *enclosing
    section*, so a relevant span is covered when it falls inside the group of any
    top-k hit. Monotonic in expansion — adding a hit's siblings can only cover
    more relevant spans, never fewer — which is why expansion is a recall
    mechanism whose cost is pack size (reported separately), not lost slots.
    """
    if not relevant:
        return 0.0, 0.0
    covered: set[int] = set()
    for spans in groups_per_rank[:k]:
        for j, rel in enumerate(relevant):
            if j not in covered and any(s.overlaps(rel) for s in spans):
                covered.add(j)
    return len(covered) / len(relevant), (1.0 if covered else 0.0)


def aggregate(
    per_query: list[dict[str, float]], *, ks: tuple[int, ...] = DEFAULT_KS
) -> dict[str, float]:
    """Mean each metric across queries. MRR is the mean of the per-query ``rr``."""
    n = len(per_query) or 1
    keys = [f"recall@{max(ks)}", *(f"recall@{k}" for k in ks), *(f"precision@{k}" for k in ks)]
    keys += [f"hit@{k}" for k in ks] + [f"ndcg@{k}" for k in ks]
    summary = {key: sum(q.get(key, 0.0) for q in per_query) / n for key in dict.fromkeys(keys)}
    summary["mrr"] = sum(q.get("rr", 0.0) for q in per_query) / n
    summary["n_queries"] = float(len(per_query))
    return summary


def percentile(values: list[float], p: float) -> float:
    """Nearest-rank percentile of ``values`` (p in [0, 100]). 0.0 when empty.

    Nearest-rank, not interpolated: with 30–40 queries the difference is noise and
    a real observed latency reads more honestly on a slide than an interpolated one.
    """
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]
