"""EmbeddingReranker — the httpx client to the llama-embed/llama-rerank
sidecars (see embedding.py's module docstring). No real model/server
involved: an ``httpx.MockTransport`` fakes the two sidecars' HTTP responses,
matching the wire shapes verified this session via real ``curl`` calls
against the running ``llama-server`` processes (see docs/claude_docs/
decisions.md) — everything except image embedding, which the module itself
flags as unverified.
"""

from __future__ import annotations

import json

import httpx
import pytest

from substrate.serving.services.extraction.embedding import (
    EmbeddingReranker,
    EmbeddingServiceError,
)


def _reranker(handler) -> EmbeddingReranker:
    reranker = EmbeddingReranker(
        embed_server_url="http://llama-embed:8031",
        rerank_server_url="http://llama-rerank:8032",
    )
    reranker._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return reranker


async def test_embed_text_returns_the_embedding_vector():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/embeddings"
        assert json.loads(request.content) == {"input": "quarterly revenue"}
        return httpx.Response(200, json=[{"embedding": [0.1, 0.2, 0.3]}])

    reranker = _reranker(handler)
    vector = await reranker.embed_text("quarterly revenue")

    assert vector == [0.1, 0.2, 0.3]


async def test_embed_text_raises_service_error_on_unexpected_shape():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    reranker = _reranker(handler)

    with pytest.raises(EmbeddingServiceError):
        await reranker.embed_text("q")


async def test_embed_text_raises_service_error_on_http_failure():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    reranker = _reranker(handler)

    with pytest.raises(EmbeddingServiceError):
        await reranker.embed_text("q")


async def test_embed_image_sends_a_data_url_content_part():
    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        part = payload["input"][0]
        assert part["type"] == "image_url"
        assert part["image_url"]["url"].startswith("data:image/png;base64,")
        return httpx.Response(200, json=[{"embedding": [0.4, 0.5]}])

    reranker = _reranker(handler)
    vector = await reranker.embed_image(b"png bytes")

    assert vector == [0.4, 0.5]


async def test_rerank_returns_scores_in_input_order_not_response_order():
    """llama-server's /rerank response is index-tagged, not positional — a
    client that assumed response order matched input order would silently
    mis-score every result after a reorder."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {
            "query": "revenue",
            "documents": ["irrelevant", "relevant"],
        }
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.1},
                ]
            },
        )

    reranker = _reranker(handler)
    scores = await reranker.rerank("revenue", ["irrelevant", "relevant"])

    assert scores == [0.1, 0.9]


async def test_rerank_empty_passages_returns_empty_list_without_a_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called for an empty passage list")

    reranker = _reranker(handler)
    assert await reranker.rerank("query", []) == []


async def test_rerank_with_image_falls_back_to_text_only_and_warns(caplog):
    """/rerank has no image field in this llama-server build (confirmed
    against the real server-context.cpp source) — passing one must not be
    silently dropped, it must degrade to text-only and say so."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "image" not in body
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.5}]})

    caplog.set_level("WARNING", logger="substrate.serving.services.extraction.embedding")
    reranker = _reranker(handler)
    scores = await reranker.rerank("q", ["passage"], image=b"png bytes")

    assert scores == [0.5]
    assert "not confirmed supported" in caplog.text


async def test_warmup_swallows_a_service_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="sidecar not up yet")

    reranker = _reranker(handler)
    await reranker.warmup()  # must not raise
