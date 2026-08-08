"""CrossEncoderReranker — reorders SearchResults by the extraction service's
local cross-encoder scores, and must degrade gracefully (fall back to
original order) on any client failure, never raise."""

from __future__ import annotations

from unittest.mock import AsyncMock

from substrate.capabilities.knowledge.reranker import CrossEncoderReranker
from substrate.kernel.core.content import TextBlock
from substrate.kernel.storage.vector import SearchResult


def _result(id_: str, text: str, score: float) -> SearchResult:
    return SearchResult(id=id_, content=[TextBlock(text=text)], score=score)


async def test_rerank_returns_results_unchanged_when_at_or_under_top_k():
    client = AsyncMock()
    reranker = CrossEncoderReranker(client)
    results = [_result("a", "revenue rose", 0.5), _result("b", "weather report", 0.4)]

    reranked = await reranker.rerank("revenue", results, top_k=5)

    assert reranked == results
    client.rerank.assert_not_awaited()


async def test_rerank_reorders_by_cross_encoder_score():
    client = AsyncMock()
    results = [
        _result("a", "weather report", 0.9),  # high vector score, low relevance
        _result("b", "revenue rose 20%", 0.3),  # low vector score, high relevance
        _result("c", "unrelated filler", 0.5),
    ]
    # Cross-encoder scores in the SAME order as `results`: b is most
    # relevant to "revenue" despite its lower vector similarity score.
    client.rerank = AsyncMock(return_value=[0.1, 0.95, 0.2])
    reranker = CrossEncoderReranker(client)

    reranked = await reranker.rerank("revenue", results, top_k=2)

    assert [r.id for r in reranked] == ["b", "c"]


async def test_rerank_falls_back_to_original_order_on_client_failure():
    client = AsyncMock()
    client.rerank = AsyncMock(return_value=None)
    results = [_result("a", "x", 0.5), _result("b", "y", 0.4), _result("c", "z", 0.3)]
    reranker = CrossEncoderReranker(client)

    reranked = await reranker.rerank("q", results, top_k=2)

    assert [r.id for r in reranked] == ["a", "b"]


async def test_rerank_falls_back_on_score_count_mismatch():
    """A malformed response (wrong-length scores list) must not raise or
    silently mis-pair scores with results — fall back to original order."""
    client = AsyncMock()
    client.rerank = AsyncMock(return_value=[0.9])  # only 1 score for 3 results
    results = [_result("a", "x", 0.5), _result("b", "y", 0.4), _result("c", "z", 0.3)]
    reranker = CrossEncoderReranker(client)

    reranked = await reranker.rerank("q", results, top_k=2)

    assert [r.id for r in reranked] == ["a", "b"]
