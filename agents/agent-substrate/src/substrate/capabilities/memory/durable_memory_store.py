"""DurableMemoryStore — Postgres-backed MemoryStore with full-text search.

Stores memories as rows in an ``agent_memories`` table. Retrieval uses
Postgres ``tsvector`` full-text search — no embeddings required.

Schema (run once via migration or ``create_tables()``)::

    CREATE TABLE agent_memories (
        id          TEXT PRIMARY KEY,
        agent_name  TEXT NOT NULL,
        content     TEXT NOT NULL,
        metadata    JSONB NOT NULL DEFAULT '{}',
        namespace   VARCHAR(255) NOT NULL DEFAULT 'default',
        search_vec  TSVECTOR GENERATED ALWAYS AS
                        (to_tsvector('english', content)) STORED,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );
    CREATE INDEX ON agent_memories USING GIN (search_vec);
    CREATE INDEX ON agent_memories (agent_name);
    CREATE INDEX ON agent_memories (namespace);
"""

from __future__ import annotations

import json
from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from substrate.kernel.core.content import (
    TextBlock,
    content_blocks_to_str,
    parse_content_block,
)
from substrate.kernel.storage.memory import (
    MemoryCategory,
    MemoryMatch,
    MemoryNamespace,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemoryStatus,
)
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


class DurableMemoryStore:
    """MemoryStore backed by Postgres full-text search.

    Retrieval ranks results by ``ts_rank`` against the search query.

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
                "DurableMemoryStore not connected — call await connect() first"
            )
        return self._engine

    def _row_to_memory(self, row: Any) -> MemoryRecord:
        meta = (
            row.metadata
            if isinstance(row.metadata, dict)
            else json.loads(row.metadata)
        )
        blocks_raw = meta.pop("_blocks", None) if isinstance(meta, dict) else None
        if blocks_raw:
            blocks = [parse_content_block(b) for b in blocks_raw]
        else:
            blocks = [TextBlock(text=row.content)]

        cat_val = meta.pop("_category", MemoryCategory.SEMANTIC.value) if isinstance(meta, dict) else MemoryCategory.SEMANTIC.value
        status_val = meta.pop("_status", MemoryStatus.ACTIVE.value) if isinstance(meta, dict) else MemoryStatus.ACTIVE.value
        ns_raw = meta.pop("_namespace", None) if isinstance(meta, dict) else None
        prov_raw = meta.pop("_provenance", None) if isinstance(meta, dict) else None

        ns = MemoryNamespace(**ns_raw) if ns_raw else MemoryNamespace(tenant_id=getattr(row, "namespace", "default"))
        prov = MemoryProvenance(**prov_raw) if prov_raw else MemoryProvenance()

        return MemoryRecord(
            id=row.id,
            content=tuple(blocks),
            category=MemoryCategory(cat_val),
            status=MemoryStatus(status_val),
            namespace=ns,
            provenance=prov,
            metadata=meta,
        )

    async def save(self, record: MemoryRecord) -> str:
        """Persist or replace a record (explicit UPSERT semantics)."""
        mem_id = record.id
        blocks = list(record.content)
        agent_str = record.namespace.user_id or record.namespace.agent_id or "default"
        ns_str = record.namespace.tenant_id or "default"
        meta = dict(record.metadata)
        meta["_category"] = record.category.value
        meta["_status"] = record.status.value
        meta["_namespace"] = {
            "tenant_id": record.namespace.tenant_id,
            "user_id": record.namespace.user_id,
            "agent_id": record.namespace.agent_id,
            "session_id": record.namespace.session_id,
        }
        meta["_provenance"] = {
            "source_session_id": record.provenance.source_session_id,
            "source_node_id": record.provenance.source_node_id,
            "source_branch_id": record.provenance.source_branch_id,
            "source_run_id": record.provenance.source_run_id,
            "confidence": record.provenance.confidence,
            "extraction_method": record.provenance.extraction_method,
            "supersedes_id": record.provenance.supersedes_id,
        }

        text_content = content_blocks_to_str(blocks)
        meta["_blocks"] = [b.model_dump(mode="json") for b in blocks]

        async with self._eng().begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO agent_memories (id, agent_name, content, metadata, namespace) "
                    "VALUES (:id, :agent_name, :content, CAST(:metadata AS jsonb), :namespace) "
                    "ON CONFLICT (id) DO UPDATE SET "
                    "content = EXCLUDED.content, metadata = EXCLUDED.metadata, "
                    "agent_name = EXCLUDED.agent_name, namespace = EXCLUDED.namespace"
                ),
                {
                    "id": mem_id,
                    "agent_name": agent_str,
                    "content": text_content,
                    "metadata": json.dumps(meta),
                    "namespace": ns_str,
                },
            )
        logger.debug(
            "[memory] saved id=%s agent=%s namespace=%s", mem_id, agent_str, ns_str
        )
        return mem_id

    async def query(self, spec: MemoryQuery) -> list[MemoryMatch]:
        """Execute structured search conforming to MemoryQuery."""
        where_clauses = ["namespace = :tenant_id"]
        params: dict[str, Any] = {"tenant_id": spec.namespace.tenant_id, "limit": spec.limit}

        if spec.namespace.user_id:
            where_clauses.append("(agent_name = :user_key OR agent_name = :user_raw)")
            params["user_key"] = f"user:{spec.namespace.user_id}"
            params["user_raw"] = spec.namespace.user_id
        elif spec.namespace.agent_id:
            where_clauses.append("(agent_name = :agent_key OR agent_name = :agent_raw)")
            params["agent_key"] = f"agent:{spec.namespace.agent_id}"
            params["agent_raw"] = spec.namespace.agent_id

        if spec.text_query:
            where_clauses.append("search_vec @@ plainto_tsquery('english', :query)")
            params["query"] = spec.text_query
            order_clause = "ts_rank(search_vec, plainto_tsquery('english', :query)) DESC"
            score_expr = "ts_rank(search_vec, plainto_tsquery('english', :query)) AS score"
        else:
            order_clause = "created_at DESC"
            score_expr = "1.0 AS score"

        sql = f"SELECT id, content, metadata, namespace, agent_name, {score_expr} FROM agent_memories WHERE {' AND '.join(where_clauses)} ORDER BY {order_clause} LIMIT :limit"

        async with self._eng().begin() as conn:
            rows = await conn.execute(text(sql), params)
            matches: list[MemoryMatch] = []
            for i, row in enumerate(rows):
                rec = self._row_to_memory(row)
                if spec.categories and rec.category not in spec.categories:
                    continue
                if spec.statuses and rec.status not in spec.statuses:
                    continue
                matches.append(MemoryMatch(record=rec, score=float(row.score), rank=i, retrieval_method="fulltext"))
            return matches

    async def touch(self, record_ids: Sequence[str]) -> None:
        """Update last_accessed_at for records."""
        if not record_ids:
            return
        async with self._eng().begin() as conn:
            await conn.execute(
                text("UPDATE agent_memories SET created_at = now() WHERE id = ANY(:ids)"),
                {"ids": list(record_ids)},
            )

    async def get(self, record_id: str) -> MemoryRecord | None:
        """Retrieve a specific record by ID."""
        sql = "SELECT id, content, metadata, namespace, agent_name FROM agent_memories WHERE id = :id"
        params = {"id": record_id}

        async with self._eng().begin() as conn:
            row = (await conn.execute(text(sql), params)).first()
        if row is None:
            return None
        return self._row_to_memory(row)

    async def delete(self, record_id: str) -> bool:
        """Permanently delete a record by ID."""
        sql = "DELETE FROM agent_memories WHERE id = :id"
        params = {"id": record_id}

        async with self._eng().begin() as conn:
            result = await conn.execute(text(sql), params)
        return result.rowcount > 0

    async def clear(self, namespace: MemoryNamespace) -> None:
        """Purge all records matching the given namespace boundary."""
        where_clauses = ["namespace = :tenant_id"]
        params: dict[str, Any] = {"tenant_id": namespace.tenant_id}
        if namespace.user_id:
            where_clauses.append("(agent_name = :user_key OR agent_name = :user_raw)")
            params["user_key"] = f"user:{namespace.user_id}"
            params["user_raw"] = namespace.user_id
        elif namespace.agent_id:
            where_clauses.append("(agent_name = :agent_key OR agent_name = :agent_raw)")
            params["agent_key"] = f"agent:{namespace.agent_id}"
            params["agent_raw"] = namespace.agent_id
        sql = f"DELETE FROM agent_memories WHERE {' AND '.join(where_clauses)}"

        async with self._eng().begin() as conn:
            await conn.execute(text(sql), params)

    async def __aenter__(self) -> DurableMemoryStore:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()


__all__ = ["DurableMemoryStore"]
