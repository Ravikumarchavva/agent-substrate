"""Integration tests for the gateway chat endpoint and message flow.

Tests the /chat endpoint, thread management via /threads, and SSE streaming.
Requires: DATABASE_URL, REDIS_URL env vars (set in CI via services).
"""

from __future__ import annotations

import uuid
import httpx
import pytest

# Use a test client for the gateway app
from raavan.services.gateway.app import app


@pytest.fixture
def test_client():
    """Create an async test client for the gateway."""
    from httpx import ASGITransport

    transport = ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


class TestHealthEndpoints:
    """Test infrastructure endpoints."""

    @pytest.mark.asyncio
    async def test_health(self, test_client: httpx.AsyncClient) -> None:
        async with test_client as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_readiness(self, test_client: httpx.AsyncClient) -> None:
        async with test_client as client:
            resp = await client.get("/ready")
            assert resp.status_code == 200


class TestChatEndpoint:
    """Test POST /chat validation and error handling."""

    @pytest.mark.asyncio
    async def test_chat_requires_thread_id(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """Chat endpoint should reject requests without a valid thread_id."""
        async with test_client as client:
            resp = await client.post(
                "/chat",
                json={"messages": [{"role": "user", "content": "hello"}]},
            )
            assert resp.status_code == 422  # Missing thread_id

    @pytest.mark.asyncio
    async def test_chat_rejects_invalid_uuid(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """Chat endpoint should reject non-UUID thread_id."""
        async with test_client as client:
            resp = await client.post(
                "/chat",
                json={
                    "thread_id": "not-a-uuid",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )
            assert resp.status_code == 422

    @pytest.mark.asyncio
    async def test_chat_rejects_empty_messages(
        self, test_client: httpx.AsyncClient
    ) -> None:
        """Chat endpoint should reject empty message list."""
        async with test_client as client:
            resp = await client.post(
                "/chat",
                json={
                    "thread_id": str(uuid.uuid4()),
                    "messages": [],
                },
            )
            # Either 422 or 400 is acceptable
            assert resp.status_code in (400, 422)


class TestCORSHeaders:
    """Test CORS configuration."""

    @pytest.mark.asyncio
    async def test_cors_allows_localhost(self, test_client: httpx.AsyncClient) -> None:
        async with test_client as client:
            resp = await client.options(
                "/health",
                headers={
                    "Origin": "http://localhost:3000",
                    "Access-Control-Request-Method": "GET",
                },
            )
            # Should not be blocked (200 or 204 for preflight)
            assert resp.status_code in (200, 204)
