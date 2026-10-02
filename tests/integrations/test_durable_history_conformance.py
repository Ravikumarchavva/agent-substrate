"""The Postgres history provider, held to the shared conformance suite. Needs Postgres."""

from __future__ import annotations

import os

import pytest

from substrate.integrations.history import DurableThreadStore
from substrate.testing.conformance.thread_store import ThreadStoreConformance

pytestmark = [pytest.mark.requires_postgres]

_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb")


class TestDurableThreadStore(ThreadStoreConformance):
    @pytest.fixture
    async def store(self):
        provider = DurableThreadStore(_URL)
        try:
            await provider.connect()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Postgres not reachable: {exc}")
        yield provider
        await provider.disconnect()
