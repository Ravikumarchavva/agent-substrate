"""The Postgres history provider, held to the shared conformance suite. Needs Postgres."""

from __future__ import annotations

import os

import pytest

from substrate.integrations.history import DurableHistoryProvider
from substrate.testing.conformance.history_provider import HistoryProviderConformance

pytestmark = [pytest.mark.requires_postgres]

_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb")


class TestDurableHistoryProvider(HistoryProviderConformance):
    @pytest.fixture
    async def store(self):
        provider = DurableHistoryProvider(_URL)
        try:
            await provider.connect()
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"Postgres not reachable: {exc}")
        yield provider
        await provider.disconnect()
