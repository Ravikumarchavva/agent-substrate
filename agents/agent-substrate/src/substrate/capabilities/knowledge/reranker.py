"""Rerankers for RAG results — an LLM-judge reranker and a local
cross-encoder reranker.

Usage::

    from substrate.capabilities.knowledge.reranker import LLMReranker

    reranker = LLMReranker(model_client=client)
    reranked = await reranker.rerank(query, results, top_k=5)

Or, with a local cross-encoder via the extraction service (no LLM
tokens/latency spent on reranking)::

    from substrate.capabilities.knowledge.reranker import CrossEncoderReranker

    reranker = CrossEncoderReranker(extraction_client)
    reranked = await reranker.rerank(query, results, top_k=5)
"""

from __future__ import annotations
from substrate.logger import setup_logging

import json
from typing import TYPE_CHECKING, Any

from substrate.kernel.storage.vector import SearchResult

if TYPE_CHECKING:
    from substrate.kernel.llm import LLMClient

logger = setup_logging()


def prefilter_candidates(
    results: list[SearchResult],
    *,
    top_n: int,
    dedup_similarity_threshold: float = 0.9,
) -> list[SearchResult]:
    """Deliberately dumb pre-filter between RRF fusion and the reranker.

    The reranker is the expensive stage (multi-second on long CPU inputs);
    this narrows a fused candidate pool (``fused_k``, typically 50) down to
    ``top_n`` (typically 10) with no learned scoring, on purpose — a
    "smarter" pre-filter that quietly becomes the new recall bottleneck is
    worse than a dumb one that doesn't. Any metadata/collection constraints
    are already applied upstream, at the store's ``hybrid_search()`` call —
    this only dedups (reusing ``citations.suppress_near_duplicates``'s >90%
    textual-overlap check) and takes the top ``top_n`` by RRF score.
    """
    from substrate.capabilities.knowledge.citations import suppress_near_duplicates

    deduped = suppress_near_duplicates(results, dedup_similarity_threshold)
    ranked = sorted(deduped, key=lambda r: r.score, reverse=True)
    return ranked[:top_n]


class LLMReranker:
    """Rerank search results using an LLM as a cross-encoder judge.

    Strategy: retrieve top-K*3 from vector search, ask the LLM to score
    each document's relevance to the query, return top-K by relevance.
    """

    def __init__(self, model_client: LLMClient) -> None:
        self._client = model_client

    async def rerank(
        self,
        query: str,
        results: list[SearchResult],
        *,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """Rerank results by LLM-judged relevance.

        Args:
            query: The user's query.
            results: Initial search results (typically over-fetched).
            top_k: Number of results to return after reranking.

        Returns:
            Top-``top_k`` results sorted by relevance (highest first).
        """
        if len(results) <= top_k:
            return results

        from substrate.kernel import ChatMessage, TextBlock

        # Build scoring prompt
        docs_block = "\n".join(
            f"[{i}] {r.to_text()[:500]}" for i, r in enumerate(results)
        )

        messages = [
            ChatMessage(
                role="user",
                content=[TextBlock(text=f"Query: {query}\n\nDocuments:\n{docs_block}")],
            ),
        ]

        try:
            from substrate.kernel.llm import GenerationOptions

            response = await self._client.generate(
                messages,
                options=GenerationOptions(
                    system_instructions=(
                        "You are a relevance judge. Given a query and numbered documents, "
                        "return a JSON array of document indices sorted by relevance to "
                        "the query (most relevant first). Return ONLY the JSON array of "
                        "integer indices, e.g. [2, 0, 4, 1, 3]."
                    )
                ),
            )
            text_parts = [b.text for b in response.content if isinstance(b, TextBlock)]
            text = "".join(text_parts)

            # Parse the JSON array of indices
            indices = json.loads(text.strip())
            if isinstance(indices, list):
                reranked: list[SearchResult] = []
                seen: set[int] = set()
                for idx in indices:
                    if (
                        isinstance(idx, int)
                        and 0 <= idx < len(results)
                        and idx not in seen
                    ):
                        reranked.append(results[idx])
                        seen.add(idx)
                    if len(reranked) >= top_k:
                        break
                return reranked

        except Exception:
            logger.warning(
                "Reranking failed, falling back to original order", exc_info=True
            )

        # Fallback: return first top_k by original score
        return results[:top_k]


class CrossEncoderReranker:
    """Rerank search results using the embedding-reranker service's local
    cross-encoder (Qwen3-VL-Reranker-2B — see runtimes/embedding_reranker/).

    Duck-types the same shape as ``LLMReranker`` (no formal Protocol exists;
    ``LocalRagBackend``'s ``reranker`` param accepts either). Unlike
    ``LLMReranker`` this doesn't burn the chat LLM's tokens/latency — one
    HTTP call to a purpose-built local model, verified at ~1ms/passage.
    """

    def __init__(self, embedding_reranker_client: Any) -> None:
        self._client = embedding_reranker_client

    async def rerank(
        self,
        query: str,
        results: list[SearchResult],
        *,
        top_k: int = 5,
    ) -> list[SearchResult]:
        if len(results) <= top_k:
            return results

        passages = [r.to_text()[:2000] for r in results]
        scores = await self._client.rerank(query, passages)
        if scores is None or len(scores) != len(results):
            logger.warning(
                "CrossEncoderReranker call failed, falling back to original order"
            )
            return results[:top_k]

        ranked = sorted(zip(scores, results), key=lambda pair: pair[0], reverse=True)
        return [result for _score, result in ranked[:top_k]]
