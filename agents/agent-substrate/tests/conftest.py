"""Shared pytest fixtures for the agent-framework test suite."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def redis_url() -> str:
    return os.getenv("REDIS_URL", "redis://localhost:6379/0")


@pytest.fixture
def database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
    )


@pytest.fixture
def system_prompt() -> str:
    return "You are a helpful agent."
