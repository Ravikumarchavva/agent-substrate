"""A SQL long-term memory behind the kernel's `MemoryStore` port.

TODO(integration): [data] adapter. Loamz Data's memory engine (semantic ranking, vectors) implements the same port; the
provider in `service.py` swaps it in and this local store stays the default for a deployment without a data service.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from datetime import UTC, datetime

from loamz.data.storage.errors import ScopeViolationError
from loamz.data.storage.memory import (
    MemoryCategory,
    MemoryMatch,
    MemoryNamespace,
    MemoryProvenance,
    MemoryQuery,
    MemoryRecord,
    MemoryStatus,
)
from loamz.data.storage.sql import Database, Row, Tx, migrate

COMPONENT = "people.memory"
SCRIPTS = [
    """
    CREATE TABLE IF NOT EXISTS people_memories (
        tenant_id TEXT NOT NULL,
        id TEXT NOT NULL,
        user_id TEXT,
        agent_id TEXT,
        session_id TEXT,
        category TEXT NOT NULL,
        status TEXT NOT NULL,
        text TEXT NOT NULL,
        content_json TEXT NOT NULL,
        provenance_json TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        importance DOUBLE PRECISION NOT NULL,
        access_count INTEGER NOT NULL,
        last_accessed_at DOUBLE PRECISION,
        created_at DOUBLE PRECISION NOT NULL,
        updated_at DOUBLE PRECISION NOT NULL,
        PRIMARY KEY (tenant_id, id)
    );
    CREATE INDEX IF NOT EXISTS people_memories_user_idx
        ON people_memories (tenant_id, user_id, updated_at)
    """,
]

_COLUMNS = (
    "id, tenant_id, user_id, agent_id, session_id, category, status, content_json, provenance_json, "
    "metadata_json, importance, access_count, last_accessed_at"
)


def _record(row: Row) -> MemoryRecord:
    accessed = row["last_accessed_at"]
    return MemoryRecord(
        id=row["id"],
        content=json.loads(row["content_json"]),
        category=MemoryCategory(row["category"]),
        status=MemoryStatus(row["status"]),
        namespace=MemoryNamespace(
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            agent_id=row["agent_id"],
            session_id=row["session_id"],
        ),
        provenance=MemoryProvenance.model_validate(json.loads(row["provenance_json"])),
        metadata=json.loads(row["metadata_json"]),
        importance=row["importance"],
        access_count=row["access_count"],
        last_accessed_at=None if accessed is None else datetime.fromtimestamp(accessed, UTC),
    )


def _owner_clause(caller: MemoryNamespace) -> tuple[str, list[object]]:
    """Records visible from `caller` (`MemoryNamespace.visible_from`): each owner field a record sets is the caller's own."""
    clauses, params = ["tenant_id = ?"], [caller.tenant_id]
    for column, value in (
        ("user_id", caller.user_id),
        ("agent_id", caller.agent_id),
        ("session_id", caller.session_id),
    ):
        if value is None:
            clauses.append(f"{column} IS NULL")
        else:
            clauses.append(f"({column} IS NULL OR {column} = ?)")
            params.append(value)
    return " AND ".join(clauses), params


class SqlMemoryStore:
    """The kernel `MemoryStore` over the agents database. Every statement names the tenant; ids are qualified by it."""

    def __init__(self, database: Database) -> None:
        self._db = database
        self._ready = False

    async def _ensure(self) -> None:
        if not self._ready:
            await migrate(self._db, COMPONENT, SCRIPTS)
            self._ready = True

    async def save(self, record: MemoryRecord) -> str:
        await self._ensure()
        ns, now = record.namespace, time.time()
        async with self._db.transaction() as tx:
            existing = await tx.fetchone(
                "SELECT user_id, agent_id, session_id FROM people_memories WHERE tenant_id = ? AND id = ?",
                ns.tenant_id,
                record.id,
            )
            if existing is not None and (
                existing["user_id"],
                existing["agent_id"],
                existing["session_id"],
            ) != (ns.user_id, ns.agent_id, ns.session_id):
                raise ScopeViolationError(
                    "That memory id belongs to another owner.", record_id=record.id
                )
            values = (
                record.category.value,
                record.status.value,
                record.text,
                json.dumps([b.model_dump(mode="json") for b in record.content]),
                record.provenance.model_dump_json(),
                json.dumps(record.metadata, default=str),
                record.importance,
                record.access_count,
                record.last_accessed_at.timestamp() if record.last_accessed_at else None,
                now,
            )
            if existing is None:
                await tx.execute(
                    "INSERT INTO people_memories (tenant_id, id, user_id, agent_id, session_id, category, "
                    "status, text, content_json, provenance_json, metadata_json, importance, access_count, "
                    "last_accessed_at, updated_at, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    ns.tenant_id,
                    record.id,
                    ns.user_id,
                    ns.agent_id,
                    ns.session_id,
                    *values,
                    now,
                )
            else:
                await tx.execute(
                    "UPDATE people_memories SET category = ?, status = ?, text = ?, content_json = ?, "
                    "provenance_json = ?, metadata_json = ?, importance = ?, access_count = ?, "
                    "last_accessed_at = ?, updated_at = ? WHERE tenant_id = ? AND id = ?",
                    *values,
                    ns.tenant_id,
                    record.id,
                )
        return record.id

    async def get(self, caller: MemoryNamespace, record_id: str) -> MemoryRecord | None:
        await self._ensure()
        where, params = _owner_clause(caller)
        async with self._db.transaction() as tx:
            row = await tx.fetchone(
                f"SELECT {_COLUMNS} FROM people_memories WHERE {where} AND id = ?",
                *params,
                record_id,
            )
        return None if row is None else _record(row)

    async def delete(self, caller: MemoryNamespace, record_id: str) -> bool:
        record = await self.get(caller, record_id)
        if record is None or not record.namespace.owned_by(caller):
            return False
        async with self._db.transaction() as tx:
            await tx.execute(
                "DELETE FROM people_memories WHERE tenant_id = ? AND id = ?",
                caller.tenant_id,
                record_id,
            )
        return True

    async def query(self, spec: MemoryQuery) -> list[MemoryMatch]:
        await self._ensure()
        if spec.tenant_wide is not None:
            where, params = "tenant_id = ?", [spec.namespace.tenant_id]
        else:
            where, params = _owner_clause(spec.namespace)
        if spec.statuses:
            where += f" AND status IN ({', '.join('?' for _ in spec.statuses)})"
            params += [s.value for s in spec.statuses]
        if spec.categories:
            where += f" AND category IN ({', '.join('?' for _ in spec.categories)})"
            params += [c.value for c in spec.categories]
        words = [w for w in (spec.text_query or "").lower().split() if w]
        async with self._db.transaction() as tx:
            rows = await tx.fetchall(
                f"SELECT {_COLUMNS}, text FROM people_memories WHERE {where} "
                "ORDER BY importance DESC, updated_at DESC, id DESC",
                *params,
            )
        matches: list[MemoryMatch] = []
        for row in rows:
            score = 1.0
            if words:
                text = row["text"].lower()
                score = sum(1 for w in words if w in text) / len(words)
                if score == 0:
                    continue
            if score >= spec.min_score:
                matches.append(
                    MemoryMatch(
                        record=_record(row),
                        score=score,
                        retrieval_method="fulltext" if words else "exact",
                    )
                )
        if words:
            matches.sort(key=lambda m: -m.score)
        matches = matches[: spec.limit]
        return [m.model_copy(update={"rank": i}) for i, m in enumerate(matches)]

    async def touch(self, caller: MemoryNamespace, record_ids: Sequence[str]) -> None:
        for record_id in record_ids:
            if await self.get(caller, record_id) is None:
                continue
            async with self._db.transaction() as tx:
                await self._touch(tx, caller.tenant_id, record_id)

    @staticmethod
    async def _touch(tx: Tx, tenant_id: str, record_id: str) -> None:
        await tx.execute(
            "UPDATE people_memories SET access_count = access_count + 1, last_accessed_at = ? "
            "WHERE tenant_id = ? AND id = ?",
            time.time(),
            tenant_id,
            record_id,
        )

    async def erase(self, within: MemoryNamespace) -> int:
        await self._ensure()
        where, params = "tenant_id = ?", [within.tenant_id]
        for column, value in (
            ("user_id", within.user_id),
            ("agent_id", within.agent_id),
            ("session_id", within.session_id),
        ):
            if value is not None:
                where += f" AND {column} = ?"
                params.append(value)
        async with self._db.transaction() as tx:
            return await tx.execute(f"DELETE FROM people_memories WHERE {where}", *params)


__all__ = ["COMPONENT", "SCRIPTS", "SqlMemoryStore"]
