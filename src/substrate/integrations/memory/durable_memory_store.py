"""DurableMemoryStore — Postgres-backed MemoryStore with full-text search.

Records live in ``memory_records``, one row per record, keyed by ``(tenant_id, id)`` so an id
is only ever meaningful inside its tenant. The owner fields (``user_id``, ``agent_id``,
``session_id``) are real columns, which is what lets visibility be decided in SQL::

    visible to caller  <=>  tenant matches
                            AND (row.user_id    IS NULL OR row.user_id    = caller.user_id)
                            AND (row.agent_id   IS NULL OR row.agent_id   = caller.agent_id)
                            AND (row.session_id IS NULL OR row.session_id = caller.session_id)

Retrieval uses Postgres ``tsvector`` full-text search — no embeddings required. The whole
record is kept as JSON beside the columns, so it comes back exactly as it went in.
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from substrate.kernel.abstractions.core.content import content_blocks_to_str
from substrate.kernel.abstractions.exceptions import ScopeViolationError
from substrate.kernel.abstractions.storage.memory import (
    MemoryMatch,
    MemoryNamespace,
    MemoryQuery,
    MemoryRecord,
)
from substrate.logger import setup_logging

logger = setup_logging()

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS memory_records (
    tenant_id        TEXT NOT NULL,
    id               TEXT NOT NULL,
    user_id          TEXT,
    agent_id         TEXT,
    session_id       TEXT,
    category         TEXT NOT NULL,
    status           TEXT NOT NULL,
    content          TEXT NOT NULL,
    record           JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_accessed_at TIMESTAMPTZ,
    access_count     INTEGER NOT NULL DEFAULT 0,
    search_vec       TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS memory_records_search_idx ON memory_records USING GIN (search_vec);
CREATE INDEX IF NOT EXISTS memory_records_owner_idx ON memory_records (tenant_id, user_id);
"""

# The caller's visibility rule, as SQL. ``CAST(:x AS TEXT)`` types the parameter when it is NULL.
_VISIBLE = (
    "tenant_id = :tenant_id"
    " AND (user_id IS NULL OR user_id = CAST(:user_id AS TEXT))"
    " AND (agent_id IS NULL OR agent_id = CAST(:agent_id AS TEXT))"
    " AND (session_id IS NULL OR session_id = CAST(:session_id AS TEXT))"
)
_WITHIN = (
    "tenant_id = :tenant_id"
    " AND (CAST(:user_id AS TEXT) IS NULL OR user_id = CAST(:user_id AS TEXT))"
    " AND (CAST(:agent_id AS TEXT) IS NULL OR agent_id = CAST(:agent_id AS TEXT))"
    " AND (CAST(:session_id AS TEXT) IS NULL OR session_id = CAST(:session_id AS TEXT))"
)


def _scope_params(scope: MemoryNamespace) -> dict[str, Any]:
    return {
        "tenant_id": scope.tenant_id,
        "user_id": scope.user_id,
        "agent_id": scope.agent_id,
        "session_id": scope.session_id,
    }


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
        """Create the ``memory_records`` table if it does not exist."""
        async with self._eng().begin() as conn:
            for stmt in _CREATE_TABLE.split(";"):
                stmt_clean = stmt.strip()
                if stmt_clean:
                    await conn.execute(text(stmt_clean))

    def _eng(self) -> AsyncEngine:
        if self._engine is None:
            raise RuntimeError("DurableMemoryStore not connected — call await connect() first")
        return self._engine

    @staticmethod
    def _row_to_record(row: Any) -> MemoryRecord:
        record = MemoryRecord.model_validate(row.record)
        return record.model_copy(update={"last_accessed_at": row.last_accessed_at, "access_count": row.access_count})

    async def save(self, record: MemoryRecord) -> str:
        """Persist or replace a record. Refuses to take over another namespace's record."""
        ns = record.namespace
        async with self._eng().begin() as conn:
            result = await conn.execute(
                text(
                    "INSERT INTO memory_records "
                    "(tenant_id, id, user_id, agent_id, session_id, category, status, content, record) "
                    "VALUES (:tenant_id, :id, :user_id, :agent_id, :session_id, :category, :status, :content, "
                    "CAST(:record AS jsonb)) "
                    "ON CONFLICT (tenant_id, id) DO UPDATE SET "
                    "category = EXCLUDED.category, status = EXCLUDED.status, content = EXCLUDED.content, "
                    "record = EXCLUDED.record "
                    "WHERE memory_records.user_id IS NOT DISTINCT FROM EXCLUDED.user_id "
                    "AND memory_records.agent_id IS NOT DISTINCT FROM EXCLUDED.agent_id "
                    "AND memory_records.session_id IS NOT DISTINCT FROM EXCLUDED.session_id "
                    "RETURNING 1"
                ),
                {
                    **_scope_params(ns),
                    "id": record.id,
                    "category": record.category.value,
                    "status": record.status.value,
                    "content": content_blocks_to_str(list(record.content)),
                    "record": record.model_dump_json(),
                },
            )
            if result.first() is None:
                raise ScopeViolationError(
                    f"record {record.id!r} already belongs to a different namespace in tenant {ns.tenant_id!r}",
                    record_id=record.id,
                )
        logger.debug("[memory] saved id=%s tenant=%s", record.id, ns.tenant_id)
        return record.id

    async def query(self, spec: MemoryQuery) -> list[MemoryMatch]:
        """Execute structured search conforming to MemoryQuery."""
        scope = spec.namespace
        if spec.tenant_wide is not None:
            logger.info("[memory] tenant-wide query on %s: %s", scope.tenant_id, spec.tenant_wide.reason)
            where = ["tenant_id = :tenant_id"]
            params: dict[str, Any] = {"tenant_id": scope.tenant_id}
        else:
            where = [_VISIBLE]
            params = _scope_params(scope)
        params["limit"] = spec.limit

        if spec.statuses:
            where.append("status = ANY(:statuses)")
            params["statuses"] = [s.value for s in spec.statuses]
        if spec.categories:
            where.append("category = ANY(:categories)")
            params["categories"] = [c.value for c in spec.categories]
        if spec.text_query:
            where.append("search_vec @@ plainto_tsquery('english', :query)")
            params["query"] = spec.text_query
            order = "ts_rank(search_vec, plainto_tsquery('english', :query)) DESC"
            score = "ts_rank(search_vec, plainto_tsquery('english', :query)) AS score"
        else:
            order = "created_at DESC"
            score = "1.0 AS score"

        sql = (
            f"SELECT record, last_accessed_at, access_count, {score} FROM memory_records "
            f"WHERE {' AND '.join(where)} ORDER BY {order} LIMIT :limit"
        )
        async with self._eng().begin() as conn:
            rows = (await conn.execute(text(sql), params)).all()
        matches: list[MemoryMatch] = []
        for i, row in enumerate(rows):
            record = self._row_to_record(row)
            if spec.metadata_filter and not all(record.metadata.get(k) == v for k, v in spec.metadata_filter.items()):
                continue
            matches.append(
                MemoryMatch(record=record, score=float(row.score), rank=i, retrieval_method="fulltext" if spec.text_query else "default")
            )
        return matches

    async def touch(self, caller: MemoryNamespace, record_ids: Sequence[str]) -> None:
        """Count an access to each visible record in ``record_ids``."""
        if not record_ids:
            return
        async with self._eng().begin() as conn:
            await conn.execute(
                text(
                    "UPDATE memory_records SET last_accessed_at = now(), access_count = access_count + 1 "
                    f"WHERE {_VISIBLE} AND id = ANY(:ids)"
                ),
                {**_scope_params(caller), "ids": list(record_ids)},
            )

    async def get(self, caller: MemoryNamespace, record_id: str) -> MemoryRecord | None:
        """The record, if it exists and is visible from ``caller``."""
        async with self._eng().begin() as conn:
            row = (
                await conn.execute(
                    text(f"SELECT record, last_accessed_at, access_count FROM memory_records WHERE {_VISIBLE} AND id = :id"),
                    {**_scope_params(caller), "id": record_id},
                )
            ).first()
        return None if row is None else self._row_to_record(row)

    async def delete(self, caller: MemoryNamespace, record_id: str) -> bool:
        """Permanently delete a record ``caller`` owns (see ``MemoryNamespace.owned_by``)."""
        async with self._eng().begin() as conn:
            result = await conn.execute(
                text(f"DELETE FROM memory_records WHERE {_VISIBLE} AND user_id IS NOT DISTINCT FROM CAST(:user_id AS TEXT) AND id = :id"),
                {**_scope_params(caller), "id": record_id},
            )
        return result.rowcount > 0

    async def erase(self, within: MemoryNamespace) -> int:
        """Remove every record under ``within``. Returns how many."""
        async with self._eng().begin() as conn:
            result = await conn.execute(text(f"DELETE FROM memory_records WHERE {_WITHIN}"), _scope_params(within))
        return int(result.rowcount)

    async def __aenter__(self) -> DurableMemoryStore:
        await self.connect()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.disconnect()


__all__ = ["DurableMemoryStore"]
