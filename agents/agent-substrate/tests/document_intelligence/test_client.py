"""ExtractionClient — every failure mode (bad status, connection error, timeout)
must come back as a structured ExtractResponse(success=False) or None, never
raise, so callers can always fall back to a lighter local extractor."""

from __future__ import annotations

import httpx

from substrate.runtimes.document_intelligence.client import ExtractionClient


def _client_with_transport(transport: httpx.MockTransport) -> ExtractionClient:
    client = ExtractionClient(base_url="http://extraction-test:8080")
    # Route through the fake transport instead of a real socket — same
    # approach httpx itself recommends for testing (MockTransport).
    client._client = httpx.AsyncClient(
        base_url="http://extraction-test:8080", transport=transport
    )
    return client


async def test_extract_success():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/extract"
        return httpx.Response(
            200,
            json={
                "success": True,
                "text": "hello world",
                "pages": [{"page_number": 1, "text": "hello world"}],
                "engine": "paddleocr",
                "page_count": 1,
            },
        )

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.extract(b"fake pdf bytes", "test.pdf", "application/pdf")

    assert result.success is True
    assert result.text == "hello world"
    assert result.engine == "paddleocr"
    assert result.pages[0].page_number == 1


async def test_extract_http_error_status_returns_failure_not_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.extract(b"data", "test.pdf", "application/pdf")

    assert result.success is False
    assert result.error is not None
    assert "500" in result.error


async def test_extract_connection_error_returns_failure_not_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.extract(b"data", "test.pdf", "application/pdf")

    assert result.success is False
    assert "Connection error" in (result.error or "")


async def test_extract_timeout_returns_failure_not_raise():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.extract(b"data", "test.pdf", "application/pdf")

    assert result.success is False
    assert "Timeout" in (result.error or "")


async def test_extract_bad_content_type_from_service():
    """400 from the service (unsupported content_type) is still just a
    structured failure to the caller, not an exception."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="Unsupported content_type")

    client = _client_with_transport(httpx.MockTransport(handler))
    result = await client.extract(b"data", "test.zip", "application/zip")

    assert result.success is False


async def test_health_true_on_200():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "ok", "pod_name": "extraction-0", "uptime_seconds": 1.0},
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
        return httpx.Response(
            200,
            json={"success": True, "text": "x", "engine": "paddleocr", "page_count": 1},
        )

    client = ExtractionClient(
        base_url="http://extraction-test:8080", auth_token="secret-token"
    )
    client._client = httpx.AsyncClient(
        base_url="http://extraction-test:8080",
        headers=client._headers,
        transport=httpx.MockTransport(handler),
    )
    await client.extract(b"data", "test.pdf", "application/pdf")

    assert seen_headers.get("authorization") == "Bearer secret-token"


async def test_close_is_idempotent():
    client = ExtractionClient()
    await client.close()
    await client.close()  # must not raise on a second call
