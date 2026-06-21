"""PostgresMemoryStore — Postgres-backed LongTermMemory with full-text search.

Stores memories as rows in an ``agent_memories`` table.  Retrieval uses
Postgres ``tsvector`` full-text search — no embeddings required.  To add
semantic (vector) retrieval, wrap a ``VectorStore`` adapter instead.

Schema (run once via migration or ``create_tables()``)::

    CREATE TABLE agent_memories (
        id          TEXT PRIMARY KEY,
        agent_name  TEXT NOT NULL,
        content     TEXT NOT NULL,
        metadata    JSONB NOT NULL DEFAULT '{}',
        search_vec  TSVECTOR GENERATED ALWAYS AS
                        (to_tsvector('english', content)) STORED,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX ON agent_memories USING GIN (search_vec);
    CREATE INDEX ON agent_memories (agent_name);

Usage::

    store = PostgresMemoryStore(database_url="postgresql+asyncpg://...")
    async with store:
        mem_id = await store.save(agent_id, "User prefers Python")
        memories = await store.search(agent_id, "language preference")
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from substrate.kernel.core.identity import AgentId
from substrate.kernel.storage.memory import Memory
from substrate.logger import setup_logging

logger = setup_logging()

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS agent_memories (
    id          TEXT PRIMARY KEY,
    agent_name  TEXT NOT NULL,
    content     TEXT NOT NULL,
    metadata    JSONB NOT NULL DEFAULT '{}',
    namespace   VARCHAR(255) NOT NULL DEFAULT 'default',
    search_vec  TSVECTOR GENERATED ALWAYS AS
                    (to_tsvector('english', content)) STORED,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS agent_memories_search_idx
    ON agent_memories USING GIN (search_vec);
CREATE INDEX IF NOT EXISTS agent_memories_agent_idx
    ON agent_memories (agent_name);
CREATE INDEX IF NOT EXISTS agent_memories_namespace_idx
    ON agent_memories (namespace);
"""


class PostgresMemoryStore:
    """LongTermMemory backed by Postgres full-text search.

    Retrieval ranks results by ``ts_rank`` against the search query so the
    most relevant memories float to the top.  No embeddings required.

    To add semantic retrieval, pair with ``VectorMemoryStore`` (wraps
    ``PgVectorStore``) and merge results in a ``HybridMemoryStore``.

    Parameters
    ----------
    database_url:
        Async SQLAlchemy URL, e.g. ``postgresql+asyncpg://user:pw@host/db``.
    """

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._engine: AsyncEngine | None = None

    async def connect(self) -> None:
        self._engine = create_async_engine(self._url, pool_pre_ping=True)

    async def disconnect(self) -> None:
        if self._engine:
            await self._engine.dispose()
            self._engine = None

    async def create_tables(self) -> None:
        """Create the ``agent_memories`` table if it does not exist."""
        async with self._eng().begin() as conn:
            for stmt in _CREATE_TABLE.split(";"):
                stmt_clean = stmt.strip()
                if stmt_clean:
                    await conn.execute(text(stmt_clean))
            await conn.execute(
                text(
                    "ALTER TABLE agent_memories ADD COLUMN IF NOT EXISTS namespace VARCHAR(255) NOT NULL DEFAULT 'default'"
                )
            )
            await conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS agent_memories_namespace_idx ON agent_memories (namespace)"
                )
            )

    def _eng(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError(
                "PostgresMemoryStore not connected — call await connect() first"
            )
        return self._engine

    async def save(
        self,
        agent_id: AgentId,
        content: str,
        *,
        namespace: str = "default",
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> str:
        mem_id = uuid.uuid4().hex
        async with self._eng().begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO agent_memories (id, agent_name, content, metadata, namespace) "
                    "VALUES (:id, :agent_name, :content, CAST(:metadata AS jsonb), :namespace)"
                ),
                {
                    "id": mem_id,
                    "agent_name": str(agent_id),
                    "content": content,
                    "metadata": json.dumps(metadata or {}),
                    "namespace": namespace,
                },
            )
        logger.debug(
            "[memory] saved id=%s agent=%s namespace=%s", mem_id, agent_id, namespace
        )
        return mem_id

    async def search(
        self,
        agent_id: AgentId,
        query: str,
        *,
        namespace: str = "default",
        limit: int = 10,
    ) -> list[Memory]:
        async with self._eng().begin() as conn:
            rows = await conn.execute(
                text(
                    "SELECT id, content, metadata, "
                    "    ts_rank(search_vec, plainto_tsquery('english', :query)) AS score "
                    "FROM agent_memories "
                    "WHERE agent_name = :agent_name AND namespace = :namespace "
                    "  AND search_vec @@ plainto_tsquery('english', :query) "
                    "ORDER BY score DESC "
                    "LIMIT :limit"
                ),
                {
                    "agent_name": str(agent_id),
                    "query": query,
                    "limit": limit,
                    "namespace": namespace,
                },
            )
            return [
                Memory(
                    id=row.id,
                    content=row.content,
                    metadata=row.metadata
                    if isinstance(row.metadata, dict)
                    else json.loads(row.metadata),
                    score=float(row.score),
                )
                for row in rows
            ]

    async def get(
        self,
        agent_id: AgentId,
        memory_id: str,
        *,
        namespace: str = "default",
    ) -> Memory | None:
        async with self._eng().begin() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT id, content, metadata FROM agent_memories "
                        "WHERE id = :id AND agent_name = :agent_name AND namespace = :namespace"
                    ),
                    {
                        "id": memory_id,
                        "agent_name": str(agent_id),
                        "namespace": namespace,
                    },
                )
            ).first()
        if row is None:
            return None
        return Memory(
            id=row.id,
            content=row.content,
            metadata=row.metadata
            if isinstance(row.metadata, dict)
            else json.loads(row.metadata),
        )

    async def delete(
        self,
        agent_id: AgentId,
        memory_id: str,
        *,
        namespace: str = "default",
    ) -> bool:
        async with self._eng().begin() as conn:
            result = await conn.execute(
                text(
                    "DELETE FROM agent_memories WHERE id = :id AND agent_name = :agent_name AND namespace = :namespace"
                ),
                {"id": memory_id, "agent_name": str(agent_id), "namespace": namespace},
            )
        return result.rowcount > 0

    async def clear(
        self,
        agent_id: AgentId,
        *,
        namespace: str = "default",
    ) -> None:
        async with self._eng().begin() as conn:
            await conn.execute(
                text(
                    "DELETE FROM agent_memories WHERE agent_name = :agent_name AND namespace = :namespace"
                ),
                {"agent_name": str(agent_id), "namespace": namespace},
            )

    async def __aenter__(self) -> PostgresMemoryStore:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()


__all__ = ["PostgresMemoryStore"]
