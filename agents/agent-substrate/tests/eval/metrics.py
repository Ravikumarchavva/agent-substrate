"""Recall@K and NDCG@K — the two metrics the RAG plan's evaluation harness
measures at each pipeline stage (Dense/Lexical/Hybrid Recall@50,
Recall@10-after-prefilter, Reranker NDCG@10, Final Recall@5).

Both take plain ``list[str]``/``set[str]`` of result ids — no dependency on
``SearchResult`` or any store, so they're testable with hand-built fake
ranked lists and reusable against any stage's output.
"""

from __future__ import annotations

import math


def recall_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """Fraction of *relevant_ids* found within the first *k* of
    *retrieved_ids*. ``0.0`` when *relevant_ids* is empty — a query with no
    labeled relevant chunk contributes nothing to the average rather than
    raising, since "nothing to find" isn't a meaningful pass/fail."""
    if not relevant_ids:
        return 0.0
    top_k = set(retrieved_ids[:k])
    return len(top_k & relevant_ids) / len(relevant_ids)


def ndcg_at_k(retrieved_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """Binary-relevance NDCG@K: each id in *relevant_ids* found in the top
    *k* of *retrieved_ids* contributes ``1 / log2(rank + 1)`` (rank 1-based),
    normalized by the best-possible ordering's DCG (all relevant ids first).
    ``0.0`` when *relevant_ids* is empty, same rationale as ``recall_at_k``.
    """
    if not relevant_ids:
        return 0.0
    dcg = sum(
        1.0 / math.log2(rank + 2)
        for rank, doc_id in enumerate(retrieved_ids[:k])
        if doc_id in relevant_ids
    )
    ideal_hits = min(len(relevant_ids), k)
    idcg = sum(1.0 / math.log2(rank + 2) for rank in range(ideal_hits))
    return dcg / idcg if idcg > 0 else 0.0
