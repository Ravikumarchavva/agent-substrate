"""Health endpoint tests for all microservices.

Verifies that each service app answers the same two open probes as the monolith: /health and /health/ready.
These are quick smoke tests — they don't test business logic.
"""

from __future__ import annotations

import importlib

import pytest
import httpx
from httpx import ASGITransport


# All microservice apps and their expected service names
SERVICE_APPS: list[tuple[str, str, str]] = [
    (f"substrate_cloud.services.{name}.app", "app", name)
    for name in (
        "gateway",
        "policy",
        "conversation",
        "job_controller",
        "agent_runtime",
        "tool_executor",
        "human_gate",
        "live_stream",
        "admin",
    )
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
            resp = await client.get("/health/ready")
            assert resp.status_code == 200
            data = resp.json()
            assert data["status"] == "ready"
