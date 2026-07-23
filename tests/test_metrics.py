"""The span-overlap retrieval metrics — pure functions, no index, no network.

These pin the scoring contract the ablation rests on: overlap in (file, line)
space, novel-coverage nDCG, and the monotonic coverage recall that makes parent
expansion legible.
"""

from __future__ import annotations

import math

from forge.evaluation.metrics import (
    Span,
    aggregate,
    coverage_recall_hit,
    percentile,
    score_query,
)


def test_overlap_requires_same_file_and_intersecting_lines():
    a = Span("x.py", 10, 20)
    assert a.overlaps(Span("x.py", 20, 30))  # touch at 20
    assert a.overlaps(Span("x.py", 5, 12))
    assert a.overlaps(Span("x.py", 12, 15))  # contained
    assert not a.overlaps(Span("x.py", 21, 30))  # just past
    assert not a.overlaps(Span("y.py", 10, 20))  # other file


def test_recall_hit_and_rr_track_the_first_overlap_rank():
    relevant = [Span("a.py", 10, 20)]
    retrieved = [Span("a.py", 100, 110), Span("z.py", 1, 2), Span("a.py", 15, 18)]
    q = score_query(retrieved, relevant, ks=(1, 2, 3))

    assert q["recall@1"] == 0.0 and q["hit@1"] == 0.0
    assert q["recall@3"] == 1.0 and q["hit@3"] == 1.0
    assert q["rr"] == 1 / 3  # first overlap at rank 3


def test_recall_is_fraction_of_a_multi_span_answer():
    relevant = [Span("a.py", 10, 20), Span("b.py", 5, 9)]
    retrieved = [Span("a.py", 12, 15)]  # covers only the first
    q = score_query(retrieved, relevant, ks=(10,))
    assert q["recall@10"] == 0.5
    assert q["hit@10"] == 1.0


def test_precision_counts_relevant_retrievals_in_top_k():
    relevant = [Span("a.py", 1, 5)]
    retrieved = [Span("a.py", 1, 5), Span("z.py", 1, 5)]  # 1 of 2 relevant
    q = score_query(retrieved, relevant, ks=(2,))
    assert q["precision@2"] == 0.5


def test_ndcg_is_one_when_a_single_answer_is_rank_one():
    relevant = [Span("a.py", 1, 5)]
    retrieved = [Span("a.py", 1, 5), Span("b.py", 1, 5)]
    q = score_query(retrieved, relevant, ks=(10,))
    assert q["ndcg@10"] == 1.0


def test_ndcg_discounts_a_later_hit():
    relevant = [Span("a.py", 1, 5)]
    retrieved = [Span("z.py", 1, 5), Span("a.py", 1, 5)]  # answer at rank 2
    q = score_query(retrieved, relevant, ks=(10,))
    assert math.isclose(q["ndcg@10"], 1 / math.log2(3))


def test_ndcg_uses_novel_coverage_not_repeated_overlaps():
    # Two retrieved chunks both overlap the one relevant span; the second earns no
    # extra gain, so nDCG is the rank-1 ideal, not inflated by the redundant hit.
    relevant = [Span("a.py", 1, 20)]
    retrieved = [Span("a.py", 1, 10), Span("a.py", 11, 20)]
    q = score_query(retrieved, relevant, ks=(10,))
    assert q["ndcg@10"] == 1.0


def test_coverage_recall_is_plain_recall_when_groups_are_singletons():
    relevant = [Span("a.py", 10, 20), Span("b.py", 1, 5)]
    groups = [[Span("a.py", 10, 20)], [Span("z.py", 9, 9)]]  # rank1 covers a, rank2 nothing
    recall, hit = coverage_recall_hit(groups, relevant, k=2)
    assert recall == 0.5 and hit == 1.0


def test_coverage_recall_credits_a_siblings_span():
    # The rank-1 hit's parent group also contains b.py's answer (a sibling), so
    # expansion covers a relevant span the bare hit would have missed.
    relevant = [Span("a.py", 10, 20), Span("a.py", 30, 40)]
    groups = [[Span("a.py", 10, 20), Span("a.py", 30, 40)]]  # one group, both spans
    recall, hit = coverage_recall_hit(groups, relevant, k=1)
    assert recall == 1.0 and hit == 1.0


def test_aggregate_means_across_queries_and_names_mrr():
    per_query = [
        {
            "recall@10": 1.0,
            "recall@5": 1.0,
            "precision@5": 0.2,
            "hit@5": 1.0,
            "hit@10": 1.0,
            "ndcg@5": 1.0,
            "ndcg@10": 1.0,
            "rr": 1.0,
        },
        {
            "recall@10": 0.0,
            "recall@5": 0.0,
            "precision@5": 0.0,
            "hit@5": 0.0,
            "hit@10": 0.0,
            "ndcg@5": 0.0,
            "ndcg@10": 0.0,
            "rr": 0.0,
        },
    ]
    agg = aggregate(per_query, ks=(5, 10))
    assert agg["recall@10"] == 0.5
    assert agg["mrr"] == 0.5
    assert agg["n_queries"] == 2.0


def test_percentile_is_nearest_rank():
    values = [0.01, 0.02, 0.03, 0.04, 0.10]
    assert percentile(values, 95) == 0.10
    assert percentile(values, 50) == 0.03
    assert percentile([], 95) == 0.0
