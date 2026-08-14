"""recall_at_k / ndcg_at_k — pure math, no infra, hand-built ranked lists."""

from __future__ import annotations

from tests.eval.metrics import ndcg_at_k, recall_at_k


def test_recall_at_k_counts_relevant_ids_within_top_k():
    retrieved = ["a", "b", "c", "d", "e"]
    relevant = {"c", "e", "z"}  # "z" is never retrieved

    assert recall_at_k(retrieved, relevant, k=5) == 2 / 3
    assert recall_at_k(retrieved, relevant, k=2) == 0.0  # neither hit is in top-2


def test_recall_at_k_is_one_when_all_relevant_ids_are_found():
    assert recall_at_k(["a", "b"], {"a", "b"}, k=5) == 1.0


def test_recall_at_k_is_zero_with_no_relevant_ids():
    assert recall_at_k(["a", "b"], set(), k=5) == 0.0


def test_ndcg_at_k_is_one_for_a_perfect_ranking():
    assert ndcg_at_k(["a", "b", "c"], {"a", "b"}, k=3) == 1.0


def test_ndcg_at_k_penalizes_a_relevant_result_ranked_lower():
    perfect = ndcg_at_k(["a", "x", "y"], {"a"}, k=3)
    worse = ndcg_at_k(["x", "a", "y"], {"a"}, k=3)
    worst = ndcg_at_k(["x", "y", "a"], {"a"}, k=3)

    assert perfect == 1.0
    assert perfect > worse > worst


def test_ndcg_at_k_ignores_results_beyond_k():
    # The one relevant id sits at rank 4, outside k=3 — must score 0, not
    # find it by scanning the whole list.
    assert ndcg_at_k(["x", "y", "z", "a"], {"a"}, k=3) == 0.0


def test_ndcg_at_k_is_zero_with_no_relevant_ids():
    assert ndcg_at_k(["a", "b"], set(), k=5) == 0.0
