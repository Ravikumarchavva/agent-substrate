"""EmbeddingReranker — the httpx client to the llama-embed/llama-rerank
sidecars (see embedding.py's module docstring). No real model/server
involved: an ``httpx.MockTransport`` fakes the two sidecars' HTTP responses,
matching the wire shapes verified against the actual running ``llama-server``
processes (see docs/claude_docs/decisions.md).
"""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from substrate.runtimes.embedding_reranker.service.embedding import (
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
        # `embedding` is a list of per-sequence vectors, not a flat vector —
        # confirmed against the real running llama-embed sidecar (with
        # --pooling last there's exactly one row per input). A prior version
        # of this test used the wrong, unverified single-nested shape, which
        # matched a real bug in embedding.py that would have hard-failed
        # Postgres's vector(2048) cast on the first real write.
        return httpx.Response(200, json=[{"embedding": [[0.1, 0.2, 0.3]]}])

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


async def test_embed_image_sends_prompt_string_and_multimodal_data():
    """The real accepted shape (confirmed against the running sidecar and
    llama.cpp's own tokenize_input_subprompt() source) — NOT the OpenAI
    chat-completions image_url content-part shape, which this server's
    /embeddings rejects outright with a 500."""
    b64_image = base64.b64encode(b"png bytes").decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/props":
            return httpx.Response(200, json={"media_marker": "<__media_test__>"})
        payload = json.loads(request.content)
        assert payload == {
            "input": {
                "prompt_string": "<__media_test__>",
                "multimodal_data": [b64_image],
            }
        }
        return httpx.Response(200, json=[{"embedding": [[0.4, 0.5]]}])

    reranker = _reranker(handler)
    vector = await reranker.embed_image(b"png bytes")

    assert vector == [0.4, 0.5]


async def test_embed_image_caches_the_media_marker_across_calls():
    props_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal props_calls
        if request.url.path == "/props":
            props_calls += 1
            return httpx.Response(200, json={"media_marker": "<__media_test__>"})
        return httpx.Response(200, json=[{"embedding": [[0.1]]}])

    reranker = _reranker(handler)
    await reranker.embed_image(b"one")
    await reranker.embed_image(b"two")

    assert props_calls == 1


async def test_embed_image_raises_service_error_when_props_unreachable():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="down")

    reranker = _reranker(handler)

    with pytest.raises(EmbeddingServiceError):
        await reranker.embed_image(b"png bytes")


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
        return httpx.Response(
            200, json={"results": [{"index": 0, "relevance_score": 0.5}]}
        )

    # `substrate.logger.setup_logging()` sets `propagate = False` on the
    # "substrate" namespace the first time any module calls it — which may
    # have already happened earlier in a full test-suite run, before this
    # test does. That blocks caplog's root-logger handler from ever seeing
    # this module's records, regardless of `caplog.set_level`. Attaching
    # caplog's handler directly to this module's logger sidesteps propagation
    # entirely, so the assertion doesn't depend on suite ordering.
    import logging

    target_logger = logging.getLogger(
        "substrate.runtimes.embedding_reranker.service.embedding"
    )
    target_logger.addHandler(caplog.handler)
    target_logger.setLevel(logging.WARNING)
    try:
        reranker = _reranker(handler)
        scores = await reranker.rerank("q", ["passage"], image=b"png bytes")
    finally:
        target_logger.removeHandler(caplog.handler)

    assert scores == [0.5]
    assert "not confirmed supported" in caplog.text


async def test_warmup_swallows_a_service_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="sidecar not up yet")

    reranker = _reranker(handler)
    await reranker.warmup()  # must not raise
