"""EmbeddingRerankerClient — every failure mode (bad status, connection error)
must come back as None, never raise, so callers can always fall back to an
unreranked order or skip indexing one bad image."""

from __future__ import annotations

import httpx

from substrate.runtimes.embedding_reranker.client import EmbeddingRerankerClient


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
