"""Shared pytest fixtures for the substrate test suite."""

from __future__ import annotations

import os

import pytest

# The safety guardrail's classifiers (PromptGuardClassifier,
# ImageSafetyClassifier) take ~2-3s each to construct — a real, unavoidable
# cost of loading a ~280MB ONNX model into memory, confirmed even fully
# offline (not a network round-trip that can be cached away). Every test
# that spins up the full app lifespan (test_workspace_routes.py,
# test_chat_stream.py, test_memory_routes.py, test_scheduled.py — ~20+
# tests combined) was paying this cost on every single test, since
# build_safety_middleware() reconstructs them fresh each time
# init_infrastructure() runs. None of those tests exercise the guardrail
# itself — that's covered directly by test_multimodal_safety_middleware.py/
# test_prompt_guard_classifier.py/test_image_safety_classifier.py, which
# don't go through the full app lifespan at all — so this loses no real
# coverage. setdefault, not a hard override: a test that explicitly wants
# the real guardrail wired into a full app can still set
# ENABLE_TEXT_SAFETY_GUARD=true in its own environment before import.
os.environ.setdefault("ENABLE_TEXT_SAFETY_GUARD", "false")

# The actual dominant cost of the app-lifespan tests turned out to be here,
# not the safety guardrail above: this repo's own .env sets
# EMBEDDING_MODEL=sentence-transformers/all-MiniLM-L6-v2 (a local torch
# model), and create_embedding_client() constructs a fresh
# SentenceTransformersEmbeddingClient — full torch + tokenizer + weights
# load — on every single full-app-lifespan test. Measured directly:
# init_llm_clients() alone was 12.7s per call (vs. 0.4s for
# init_infrastructure(), which is everything else including the safety
# guardrail). None of the app-lifespan test files touch embeddings at all
# (confirmed by grep); the one file that genuinely needs real embeddings
# (tests/eval/test_retrieval_eval.py) constructs its own
# OpenAIEmbeddingClient directly rather than reading this setting, so
# overriding the default here doesn't weaken that test.
os.environ.setdefault("EMBEDDING_MODEL", "text-embedding-3-small")

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
    config.addinivalue_line(
        "markers",
        "requires_model_download: downloads a real model from the HF Hub "
        "(100MB+) — skip in offline/constrained CI via SKIP_MODEL_DOWNLOAD_TESTS=1",
    )


@pytest.fixture(autouse=True)
async def _skip_if_infra_missing(request: pytest.FixtureRequest) -> None:
    """Auto-skip any test marked requires_postgres / requires_redis /
    requires_model_download."""
    if request.node.get_closest_marker("requires_postgres"):
        if not await _check_pg():
            pytest.skip("Postgres not reachable — run `make infra-up` first")
    if request.node.get_closest_marker("requires_redis"):
        if not await _check_redis():
            pytest.skip("Redis not reachable — run `make infra-up` first")
    if request.node.get_closest_marker("requires_model_download"):
        if os.environ.get("SKIP_MODEL_DOWNLOAD_TESTS"):
            pytest.skip("SKIP_MODEL_DOWNLOAD_TESTS=1")


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
