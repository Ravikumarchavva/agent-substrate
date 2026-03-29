"""Health endpoint tests for all microservices.

Verifies that each service app can start and responds to /health and /ready.
These are quick smoke tests — they don't test business logic.
"""

from __future__ import annotations

import importlib

import pytest
import httpx
from httpx import ASGITransport


# All microservice apps and their expected service names
SERVICE_APPS: list[tuple[str, str, str]] = [
    ("raavan.services.gateway.app", "app", "Gateway BFF"),
    ("raavan.services.conversation.app", "app", "Conversation"),
    ("raavan.services.live_stream.app", "app", "Live Stream"),
    ("raavan.services.admin.app", "app", "Admin"),
]


@pytest.fixture(params=SERVICE_APPS, ids=[s[2] for s in SERVICE_APPS])
def service_app(request: pytest.FixtureRequest) -> tuple[object, str]:
    """Parameterized fixture that yields each service's ASGI app."""
    module_path, attr_name, service_name = request.param
    try:
        mod = importlib.import_module(module_path)
        app = getattr(mod, attr_name)
        return app, service_name
    except Exception as e:
        pytest.skip(f"Cannot import {module_path}: {e}")


class TestServiceHealth:
    @pytest.mark.asyncio
    async def test_health_endpoint(self, service_app: tuple[object, str]) -> None:
        app, name = service_app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ok"

    @pytest.mark.asyncio
    async def test_ready_endpoint(self, service_app: tuple[object, str]) -> None:
        app, name = service_app
        transport = ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            resp = await client.get("/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ready"
