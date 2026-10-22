"""Fixtures every test of loamz-agents may use."""

from __future__ import annotations

import os

import pytest

from loamz.engine.testing import postgres_available, postgres_schema, sqlite_database

REDIS_URL = os.environ.get("LOAMZ_TEST_REDIS_URL", "redis://localhost:6379/0")
_redis_available: bool | None = None


async def _check_redis() -> bool:
    global _redis_available
    if _redis_available is None:
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(REDIS_URL, socket_connect_timeout=2)
            await client.ping()
            await client.aclose()
            _redis_available = True
        except Exception:  # noqa: BLE001 - any failure means "not here"
            _redis_available = False
    return _redis_available


@pytest.fixture(params=["sqlite", "postgres"])
async def db(request):
    """Stores run on both databases: their SQL is written once. PostgreSQL needs LOAMZ_TEST_POSTGRES_URL."""
    if request.param == "sqlite":
        async with sqlite_database() as database:
            yield database
    else:
        if not await postgres_available():
            pytest.skip("no test PostgreSQL (set LOAMZ_TEST_POSTGRES_URL)")
        async with postgres_schema() as database:
            yield database


@pytest.fixture(autouse=True)
async def _skip_without_redis(request: pytest.FixtureRequest) -> None:
    if request.node.get_closest_marker("requires_redis") and not await _check_redis():
        pytest.skip("Redis not reachable (LOAMZ_TEST_REDIS_URL)")


@pytest.fixture
def redis_url() -> str:
    return REDIS_URL


@pytest.fixture
def system_prompt() -> str:
    return "You are a helpful agent."
