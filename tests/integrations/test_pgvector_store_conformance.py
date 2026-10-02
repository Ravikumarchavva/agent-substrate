"""The pgvector store, held to the shared conformance suite. Needs Postgres (``make infra-up``)."""

from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from substrate.integrations.vector.pgvector_store import PgVectorStore
from substrate.testing.conformance.vector_store import VectorStoreConformance

pytestmark = [pytest.mark.requires_postgres]

_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb")


class TestPgVectorStore(VectorStoreConformance):
    dimensions = 8

    @pytest.fixture
    async def store(self):
        engine = create_async_engine(_URL, pool_pre_ping=True)
        table = f"conformance_{uuid.uuid4().hex[:12]}"
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - no database to test against
            await engine.dispose()
            pytest.skip(f"Postgres not reachable: {exc}")
        store = PgVectorStore(session_factory=factory, engine=engine, dimensions=self.dimensions, table_name=table)
        await store.ensure_table()
        yield store
        async with engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {table}"))
        await engine.dispose()
