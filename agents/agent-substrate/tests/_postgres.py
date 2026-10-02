"""A store on PostgreSQL in a throw-away schema: each test gets an empty one, dropped afterwards."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

from substrate.integrations.database import postgres_store
from substrate.stores import Store

DSN = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agentdb"
).replace("+asyncpg", "")


async def schema_store(
    files: Path, *, file_quota_bytes: int | None = None
) -> AsyncGenerator[Store]:
    import asyncpg

    schema = f"t_{uuid.uuid4().hex[:12]}"
    store = postgres_store(
        DSN,
        files=files,
        schema=schema,
        file_quota_bytes=file_quota_bytes,
        pool_min_size=1,
        pool_max_size=4,
    )
    await store.start()
    try:
        yield store
    finally:
        await store.aclose()
        conn = await asyncpg.connect(DSN)
        try:
            await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        finally:
            await conn.close()
