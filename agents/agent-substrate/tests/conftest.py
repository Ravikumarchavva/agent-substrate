"""Shared pytest fixtures for the ravi test suite."""

from __future__ import annotations

import os

import pytest

_PG_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agentdb"
).replace("+asyncpg", "")
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Session-level reachability cache — probe once, skip the whole run for each mark.
_pg_available: bool | None = None
_redis_available: bool | None = None


async def _check_pg() -> bool:
    global _pg_available
    if _pg_available is not None:
        return _pg_available
    try:
        import asyncpg

        pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=1, timeout=3)
        await pool.close()
        _pg_available = True
    except Exception:
        _pg_available = False
    return _pg_available


async def _check_redis() -> bool:
    global _redis_available
    if _redis_available is not None:
        return _redis_available
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(_REDIS_URL, socket_connect_timeout=3)
        await client.ping()
        await client.aclose()
        _redis_available = True
    except Exception:
        _redis_available = False
    return _redis_available


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "requires_postgres: skip when Postgres is not reachable"
    )
    config.addinivalue_line(
        "markers", "requires_redis: skip when Redis is not reachable"
    )


@pytest.fixture(autouse=True)
async def _skip_if_infra_missing(request: pytest.FixtureRequest) -> None:
    """Auto-skip any test marked requires_postgres / requires_redis."""
    if request.node.get_closest_marker("requires_postgres"):
        if not await _check_pg():
            pytest.skip("Postgres not reachable — run `make infra-up` first")
    if request.node.get_closest_marker("requires_redis"):
        if not await _check_redis():
            pytest.skip("Redis not reachable — run `make infra-up` first")


# ---------------------------------------------------------------------------
# Convenience fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_url() -> str:
    return _REDIS_URL


@pytest.fixture
def database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
    )


@pytest.fixture
def system_prompt() -> str:
    return "You are a helpful agent."
