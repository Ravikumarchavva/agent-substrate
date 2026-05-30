"""LLM-based reranker for RAG results.

Uses an existing model client to score query-document relevance —
no external reranking model required.

Usage::

    from ravi.catalog.rag.reranker import LLMReranker

    reranker = LLMReranker(model_client=client)
    reranked = await reranker.rerank(query, results, top_k=5)
"""

from __future__ import annotations
from ravi.logger import setup_logging

import json
from typing import TYPE_CHECKING

from ravi.capabilities.knowledge.vector_store import SearchResult

if TYPE_CHECKING:
    from ravi.kernel.llm import LLMClient

logger = setup_logging()


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

        from ravi.kernel import ChatMessage, TextBlock

        # Build scoring prompt
        docs_block = "\n".join(f"[{i}] {r.text[:500]}" for i, r in enumerate(results))

        messages = [
            ChatMessage(
                role="user",
                content=[TextBlock(text=f"Query: {query}\n\nDocuments:\n{docs_block}")],
            ),
        ]

        try:
            response = await self._client.generate(
                messages,
                system=(
                    "You are a relevance judge. Given a query and numbered documents, "
                    "return a JSON array of document indices sorted by relevance to "
                    "the query (most relevant first). Return ONLY the JSON array of "
                    "integer indices, e.g. [2, 0, 4, 1, 3]."
                ),
            )
            text_parts = [b.text for b in response if isinstance(b, TextBlock)]
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
