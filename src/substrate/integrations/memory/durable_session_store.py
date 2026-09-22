"""DurableSessionStore — Postgres-backed ShortTermMemory (JSONB).

The durable backend the kernel protocol's own docstring already names
("Backed by: Redis HASH, Postgres JSONB, in-memory dict") but that never
existed as code — this fills that gap. Pairs with ``RedisSessionStore``
behind ``CachedShortTermMemory`` for a fast-cache-in-front-of-durable-store
setup; usable standalone wherever durability matters more than raw speed.

Schema (created automatically via ``connect()``)::

    CREATE TABLE session_state (
        session_id  TEXT PRIMARY KEY,
        state       JSONB NOT NULL DEFAULT '{}',
        updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

Usage::

    store = DurableSessionStore(database_url="postgresql+asyncpg://...")
    async with store:
        await store.update_state("sess-123", {"preferred_language": "Python"})
        state = await store.get_state("sess-123")
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from substrate.logger import setup_logging

logger = setup_logging()

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS session_state (
    session_id  TEXT PRIMARY KEY,
    state       JSONB NOT NULL DEFAULT '{}',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class DurableSessionStore:
    """ShortTermMemory backed by a Postgres JSONB column.

    ``update_state`` merges via Postgres's ``||`` JSONB operator inside a
    single statement rather than read-modify-write, so concurrent patches to
    different keys in the same session never clobber each other.

    Parameters
    ----------
    database_url:
        Async SQLAlchemy URL, e.g. ``postgresql+asyncpg://user:pw@host/db``.
    """

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._engine: AsyncEngine | None = None

    async def connect(self) -> None:
        if self._engine is not None:
            return
        self._engine = create_async_engine(self._url, pool_pre_ping=True)
        async with self._eng().begin() as conn:
            await conn.execute(text(_CREATE_TABLE))
        logger.info("DurableSessionStore connected and table ensured")

    async def disconnect(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()
            self._engine = None

    def _eng(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError(
                "DurableSessionStore not connected — call await connect() first"
            )
        return self._engine

    async def get_state(self, session_id: str) -> dict[str, Any]:
        async with self._eng().begin() as conn:
            row = (
                await conn.execute(
                    text("SELECT state FROM session_state WHERE session_id = :sid"),
                    {"sid": session_id},
                )
            ).first()
        return dict(row.state) if row is not None else {}

    async def set_state(self, session_id: str, state: dict[str, Any]) -> None:
        import json

        async with self._eng().begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO session_state (session_id, state, updated_at) "
                    "VALUES (:sid, CAST(:state AS jsonb), now()) "
                    "ON CONFLICT (session_id) DO UPDATE "
                    "SET state = CAST(:state AS jsonb), updated_at = now()"
                ),
                {"sid": session_id, "state": json.dumps(state)},
            )

    async def update_state(self, session_id: str, patch: dict[str, Any]) -> None:
        if not patch:
            return
        import json

        async with self._eng().begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO session_state (session_id, state, updated_at) "
                    "VALUES (:sid, CAST(:patch AS jsonb), now()) "
                    "ON CONFLICT (session_id) DO UPDATE "
                    "SET state = session_state.state || CAST(:patch AS jsonb), "
                    "    updated_at = now()"
                ),
                {"sid": session_id, "patch": json.dumps(patch)},
            )

    async def clear(self, session_id: str) -> None:
        async with self._eng().begin() as conn:
            await conn.execute(
                text("DELETE FROM session_state WHERE session_id = :sid"),
                {"sid": session_id},
            )

    async def __aenter__(self) -> DurableSessionStore:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()


__all__ = ["DurableSessionStore"]
