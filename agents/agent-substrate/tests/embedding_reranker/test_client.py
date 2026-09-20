"""EmbeddingRerankerClient — every failure mode (bad status, connection error)
must come back as None, never raise, so callers can always fall back to an
unreranked order or skip indexing one bad image."""

from __future__ import annotations

import json

import httpx2 as httpx
import pytest

from substrate.kernel.core.content import MediaBlock, TextBlock
from substrate.kernel.exceptions import UnsupportedContentError
from substrate.runtimes.embedding_reranker.client import (
    EmbeddingRerankerClient,
    EmbeddingRerankerTextEmbeddingClient,
)


def _client_with_transport(transport: httpx.MockTransport) -> EmbeddingRerankerClient:
    client = EmbeddingRerankerClient(base_url="http://embedding-reranker-test:8080")
    client._client = httpx.AsyncClient(
        base_url="http://embedding-reranker-test:8080", transport=transport
    )
    return client


async def test_embed_image_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embed"
        return httpx.Response(200, json={"embedding": [0.1, 0.2, 0.3]})

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.embed_image(b"fake png bytes")

    assert result == [0.1, 0.2, 0.3]


async def test_embed_text_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/embed"
        return httpx.Response(200, json={"embedding": [0.4, 0.5]})

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.embed_text("revenue chart")

    assert result == [0.4, 0.5]


async def test_embed_returns_none_on_failure_not_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.embed_text("query")

    assert result is None


async def test_rerank_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/rerank"
        return httpx.Response(200, json={"scores": [0.9, 0.1]})

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.rerank("query", ["passage a", "passage b"])

    assert result == [0.9, 0.1]


async def test_rerank_empty_passages_short_circuits():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not make an HTTP call for empty passages")

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.rerank("query", [])

    assert result == []


async def test_rerank_returns_none_on_failure_not_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.rerank("query", ["passage"])

    assert result is None


async def test_health_true_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "pod_name": "embedding-reranker-0",
                "uptime_seconds": 1.0,
            },
        )

    client = _client_with_transport(httpx.MockTransport(handler))
    assert await client.health() is True


async def test_health_false_on_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _client_with_transport(httpx.MockTransport(handler))
    assert await client.health() is False


async def test_auth_header_sent_when_token_configured():
    seen_headers: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, json={"embedding": [0.1]})

    client = EmbeddingRerankerClient(
        base_url="http://embedding-reranker-test:8080", auth_token="secret-token"
    )
    client._client = httpx.AsyncClient(
        base_url="http://embedding-reranker-test:8080",
        headers=client._headers,
        transport=httpx.MockTransport(handler),
    )
    await client.embed_text("query")

    assert seen_headers.get("authorization") == "Bearer secret-token"


async def test_close_is_idempotent():
    client = EmbeddingRerankerClient()
    await client.close()
    await client.close()  # must not raise on a second call


# ── EmbeddingRerankerTextEmbeddingClient (kernel EmbeddingClient adapter) ──


async def test_adapter_embed_batches_via_repeated_embed_text_calls():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"embedding": [0.1, 0.2]})

    client = _client_with_transport(httpx.MockTransport(handler))
    adapter = EmbeddingRerankerTextEmbeddingClient(client, model="qwen3-vl-embedding-2b")

    result = await adapter.embed(["a", "b", "c"])

    assert len(calls) == 3
    assert result.embeddings == [[0.1, 0.2], [0.1, 0.2], [0.1, 0.2]]
    assert result.model == "qwen3-vl-embedding-2b"


async def test_adapter_embed_single_returns_the_raw_vector():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embedding": [0.7, 0.8]})

    client = _client_with_transport(httpx.MockTransport(handler))
    adapter = EmbeddingRerankerTextEmbeddingClient(client)

    assert await adapter.embed_single("query") == [0.7, 0.8]


async def test_embed_blocks_mixed_text_and_image_hits_real_endpoint_shape():
    """Mixed text+image must go through the real /v1/embed shape
    (text + images_base64 together) — not the old nonexistent
    multimodal_data key that silently dropped images server-side."""
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"embedding": [0.1, 0.2]})

    client = _client_with_transport(httpx.MockTransport(handler))
    blocks = [TextBlock(text="a chart"), MediaBlock.image(data=b"pngbytes")]

    result = await client.embed_blocks(blocks)

    assert result == [0.1, 0.2]
    assert seen["text"] == "a chart"
    assert seen["images_base64"] == ["cG5nYnl0ZXM="]  # base64("pngbytes")
    assert "multimodal_data" not in seen


async def test_embed_blocks_image_only_succeeds_via_real_endpoint():
    """Images with no text must not 400 by accident — they hit the real
    images_base64-only shape, not a guessed field the server ignores."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert "text" not in body
        assert body["images_base64"] == ["cG5nYnl0ZXM="]
        return httpx.Response(200, json={"embedding": [0.3, 0.4]})

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.embed_blocks([MediaBlock.image(data=b"pngbytes")])

    assert result == [0.3, 0.4]


async def test_embed_blocks_text_only_uses_plain_embed_text_path():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body == {"text": "just text"}
        return httpx.Response(200, json={"embedding": [0.5]})

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.embed_blocks([TextBlock(text="just text")])

    assert result == [0.5]


async def test_embed_blocks_falls_back_to_mean_pool_on_real_endpoint_failure():
    """Fallback is only for a genuine failure of the mixed endpoint, not the
    accidental-400 path the old broken implementation relied on."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        body = json.loads(request.content)
        if "images_base64" in body:
            return httpx.Response(500, text="mixed embed unavailable")
        return httpx.Response(200, json={"embedding": [1.0, 0.0]})

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.embed_blocks(
        [TextBlock(text="q"), MediaBlock.image(data=b"pngbytes")]
    )

    assert result is not None
    assert len(calls) == 3  # 1 failed mixed attempt + 2 per-modality fallback calls


async def test_embed_blocks_raises_on_unresolved_media_url():
    with pytest.raises(UnsupportedContentError):
        await EmbeddingRerankerClient().embed_blocks(
            [MediaBlock.image(url="https://example.com/a.png")]
        )


async def test_adapter_embed_raises_on_underlying_failure_not_silent_none():
    """Unlike the raw client's own embed_text() (which returns None on
    failure for callers built to skip-and-continue), the EmbeddingClient
    Protocol has no None-return contract -- an adapter caller expects a
    real EmbeddingResult or an exception, so a failure must raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = _client_with_transport(httpx.MockTransport(handler))
    adapter = EmbeddingRerankerTextEmbeddingClient(client)

    with pytest.raises(RuntimeError):
        await adapter.embed(["a"])
    with pytest.raises(RuntimeError):
        await adapter.embed_single("a")
