"""Runs the starter dataset through each real retrieval stage and averages
Recall@K/NDCG@K per stage across all queries — the per-stage breakdown the
RAG plan's evaluation harness calls for (§Evaluation harness), so a
regression is traceable to *which* stage caused it, not just that the
end-to-end number moved.

Stages measured, in pipeline order:
  1. Dense-only  (``Vectors.search``)                   → Recall@dense_k
  2. Lexical-only (``Vectors.lexical_search``)           → Recall@lexical_k
  3. Hybrid (RRF-fused)  (``Vectors.hybrid_search``) → Recall@fused_k
  4. Candidates  (the best ``rerank_top_n`` of the fused list) → Recall@rerank_top_n
  5. Reranker    (any ``Reranker``)                      → NDCG@final_k
  6. Final       (whatever rerank produces)             → Recall@final_k

Categories (text→text vs. text→image) are reported separately as well as
combined, per the plan's point 9 — checking that hybrid search and the
reranker aren't just working for prose.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from tests.eval.dataset import EvalDataset
from tests.eval.metrics import ndcg_at_k, recall_at_k

if TYPE_CHECKING:
    from substrate.stores.vector_tables import Vectors
    from substrate.models import EmbeddingModel


@dataclass(slots=True)
class StageMetrics:
    dense_recall: float
    lexical_recall: float
    hybrid_recall: float
    prefilter_recall: float
    reranker_ndcg: float
    final_recall: float
    n_queries: int


@dataclass(slots=True)
class EvalReport:
    overall: StageMetrics
    by_category: dict[str, StageMetrics] = field(default_factory=dict)


def _average(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


async def run_retrieval_eval(
    *,
    store: "Vectors",
    embedding_client: "EmbeddingModel",
    dataset: EvalDataset,
    collection: str,
    reranker: Any | None = None,
    dense_k: int = 50,
    lexical_k: int = 50,
    fused_k: int = 50,
    rerank_top_n: int = 10,
    final_k: int = 5,
) -> EvalReport:
    per_query: list[tuple[str, dict[str, float]]] = []

    for eval_query in dataset.queries:
        relevant = eval_query.relevant_doc_ids
        query_vec = (await embedding_client.embed([eval_query.query], query=True)).embeddings[0]

        dense = await store.search(query_vec, collection=collection, limit=dense_k)
        lexical = await store.lexical_search(
            eval_query.query, collection=collection, limit=lexical_k
        )
        hybrid = await store.hybrid_search(
            query_vec,
            eval_query.query,
            collection=collection,
            dense_k=dense_k,
            lexical_k=lexical_k,
            fused_k=fused_k,
        )
        prefiltered = sorted(hybrid, key=lambda r: r.score, reverse=True)[:rerank_top_n]
        if reranker is not None and prefiltered:
            scores = await reranker.rerank(eval_query.query, [r.to_text() for r in prefiltered])
            final = [r for _s, r in sorted(zip(scores, prefiltered, strict=True), key=lambda pair: -pair[0])][:final_k]
        else:
            final = prefiltered[:final_k]

        metrics = {
            "dense_recall": recall_at_k([r.id for r in dense], relevant, dense_k),
            "lexical_recall": recall_at_k([r.id for r in lexical], relevant, lexical_k),
            "hybrid_recall": recall_at_k([r.id for r in hybrid], relevant, fused_k),
            "prefilter_recall": recall_at_k(
                [r.id for r in prefiltered], relevant, rerank_top_n
            ),
            "reranker_ndcg": ndcg_at_k([r.id for r in final], relevant, final_k),
            "final_recall": recall_at_k([r.id for r in final], relevant, final_k),
        }
        per_query.append((eval_query.category, metrics))

    def _aggregate(rows: list[dict[str, float]]) -> StageMetrics:
        return StageMetrics(
            dense_recall=_average([r["dense_recall"] for r in rows]),
            lexical_recall=_average([r["lexical_recall"] for r in rows]),
            hybrid_recall=_average([r["hybrid_recall"] for r in rows]),
            prefilter_recall=_average([r["prefilter_recall"] for r in rows]),
            reranker_ndcg=_average([r["reranker_ndcg"] for r in rows]),
            final_recall=_average([r["final_recall"] for r in rows]),
            n_queries=len(rows),
        )

    overall = _aggregate([m for _cat, m in per_query])
    by_category: dict[str, StageMetrics] = {}
    for category in {cat for cat, _m in per_query}:
        by_category[category] = _aggregate(
            [m for cat, m in per_query if cat == category]
        )

    return EvalReport(overall=overall, by_category=by_category)
