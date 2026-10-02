"""Memory — long-term records and per-session state, kept in the store's database.

``Memory`` is the one implementation of ``MemoryStore`` (``stores/memory.py``): who may see and change a record is
decided here once, in the statements themselves, not re-derived by each caller:

* every read and delete names the *caller's* namespace, and a record the caller cannot see behaves exactly as if it
  did not exist — the visibility rule (``MemoryNamespace.visible_from``) is a ``WHERE`` clause, so there is no
  code path that fetches first and checks later;
* a record's id is qualified by its tenant: another tenant's record with the same id is a different row;
* saving over a record of a different namespace in the same tenant is refused (``ScopeViolationError``);
* erasing removes the rows, then the full-text index entries, then every trace the database's own files keep of them
  (``Database.reclaim``) — "removed" means unrecoverable from the folder, not merely hidden.

Search is full text (SQLite FTS5, porter stemming): a query is its words, all of which must match — ``tea`` finds
"likes green tea" and ``running`` finds "I run daily". No embeddings are needed.

``SessionState`` is the small key/value state one conversation keeps across runs (``ShortTermMemory``).
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, TypeVar

from substrate.stores import textsearch
from substrate.stores.database import Database, Row, Tx
from substrate.stores.memory import (
    MemoryMatch,
    MemoryNamespace,
    MemoryQuery,
    MemoryRecord,
)
from substrate.types.content import TextBlock
from substrate.types.errors import ScopeViolationError

if TYPE_CHECKING:
    from substrate.stores.store import Store

T = TypeVar("T")


def _memory_schema(database: Database) -> str:
    return (
        """
CREATE TABLE IF NOT EXISTS memory_records (
    seq {pk},
    tenant_id TEXT NOT NULL,
    id TEXT NOT NULL,
    user_id TEXT,
    agent_id TEXT,
    session_id TEXT,
    category TEXT NOT NULL,
    status TEXT NOT NULL,
    text TEXT NOT NULL,
    record_json TEXT NOT NULL,
    last_accessed_at TEXT,
    access_count INTEGER NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    UNIQUE (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS memory_records_owner_idx ON memory_records (tenant_id, user_id);
"""
        + textsearch.ddl(database.dialect, index="memory_fts", table="memory_records")
        + """
CREATE TABLE IF NOT EXISTS session_state (
    session_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);
"""
    )


MEMORY_SCHEMA = [_memory_schema]

_COLUMNS = (
    "tenant_id, id, user_id, agent_id, session_id, category, status, text, record_json, "
    "last_accessed_at, access_count, created_at"
)


def _text_of(record: MemoryRecord) -> str:
    return " ".join(
        block.text
        for block in record.content
        if isinstance(block, TextBlock) and block.text
    )


def _record(row: Row) -> MemoryRecord:
    """The stored record, with the counters the database keeps current laid over the saved copy."""
    data = json.loads(row["record_json"])
    data["last_accessed_at"] = row["last_accessed_at"]
    data["access_count"] = row["access_count"]
    return MemoryRecord.model_validate(data)


def _visible(caller: MemoryNamespace, alias: str = "") -> tuple[str, list[Any]]:
    """The ``MemoryNamespace.visible_from`` rule as SQL: every owner field a record sets must be the caller's own."""
    p = f"{alias}." if alias else ""
    clause = (
        f"{p}tenant_id = ? AND ({p}user_id IS NULL OR {p}user_id = ?) AND ({p}agent_id IS NULL OR {p}agent_id = ?) "
        f"AND ({p}session_id IS NULL OR {p}session_id = ?)"
    )
    return clause, [
        caller.tenant_id,
        caller.user_id,
        caller.agent_id,
        caller.session_id,
    ]


def _in(column: str, values: Sequence[Any]) -> tuple[str, list[Any]]:
    return f"{column} IN ({', '.join('?' for _ in values)})", list(values)


class Memory:
    """The ``MemoryStore`` of a ``Store``: ``store.memory``."""

    def __init__(self, store: Store) -> None:
        self._store = store

    @property
    def store(self) -> Store:
        """The store this memory lives in — for a host that opened it and has to close it."""
        return self._store

    async def _run(self, fn: Callable[[Tx], Awaitable[T]]) -> T:
        return await self._store.run(fn)

    async def save(self, record: MemoryRecord) -> str:
        ns = record.namespace

        async def op(tx: Tx) -> str:
            await tx.lock(f"memory:{ns.tenant_id}:{record.id}")
            existing = await tx.fetchone(
                "SELECT user_id, agent_id, session_id FROM memory_records WHERE tenant_id = ? AND id = ?",
                ns.tenant_id,
                record.id,
            )
            if existing is not None and (
                existing["user_id"],
                existing["agent_id"],
                existing["session_id"],
            ) != (
                ns.user_id,
                ns.agent_id,
                ns.session_id,
            ):
                raise ScopeViolationError(
                    f"record {record.id!r} already belongs to a different namespace in tenant {ns.tenant_id!r}",
                    record_id=record.id,
                )
            await tx.execute(
                f"INSERT INTO memory_records ({_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (tenant_id, id) DO UPDATE SET category = excluded.category, status = excluded.status, "
                "text = excluded.text, record_json = excluded.record_json, "
                "last_accessed_at = excluded.last_accessed_at, access_count = excluded.access_count",
                ns.tenant_id,
                record.id,
                ns.user_id,
                ns.agent_id,
                ns.session_id,
                record.category.value,
                record.status.value,
                _text_of(record),
                record.model_dump_json(),
                record.last_accessed_at.isoformat()
                if record.last_accessed_at
                else None,
                record.access_count,
                time.time(),
            )
            return record.id

        return await self._run(op)

    async def get(self, caller: MemoryNamespace, record_id: str) -> MemoryRecord | None:
        async def op(tx: Tx) -> MemoryRecord | None:
            visible, params = _visible(caller)
            row = await tx.fetchone(
                f"SELECT * FROM memory_records WHERE {visible} AND id = ?",
                *params,
                record_id,
            )
            return _record(row) if row else None

        return await self._run(op)

    async def delete(self, caller: MemoryNamespace, record_id: str) -> bool:
        async def op(tx: Tx) -> bool:
            visible, params = _visible(caller)
            row = await tx.fetchone(
                f"SELECT * FROM memory_records WHERE {visible} AND id = ?",
                *params,
                record_id,
            )
            if row is None or not _record(row).namespace.owned_by(caller):
                return False
            await tx.execute(
                "DELETE FROM memory_records WHERE tenant_id = ? AND id = ?",
                caller.tenant_id,
                record_id,
            )
            return True

        return await self._run(op)

    async def query(self, spec: MemoryQuery) -> list[MemoryMatch]:
        if not spec.statuses:
            return []
        caller = spec.namespace

        async def op(tx: Tx) -> list[MemoryMatch]:
            if spec.tenant_wide is not None:
                clauses, params = ["r.tenant_id = ?"], [caller.tenant_id]
            else:
                visible, params = _visible(caller, "r")
                clauses = [visible]
            status, status_params = _in("r.status", [s.value for s in spec.statuses])
            clauses.append(status)
            params += status_params
            if spec.categories is not None:
                if not spec.categories:
                    return []
                category, category_params = _in(
                    "r.category", [c.value for c in spec.categories]
                )
                clauses.append(category)
                params += category_params

            if spec.text_query:
                words = textsearch.words(spec.text_query)
                if not words:
                    return []
                dialect = self._store.database.dialect
                source, match, match_params = textsearch.ranked(
                    dialect,
                    index="memory_fts",
                    table="memory_records",
                    alias="r",
                    query_words=words,
                )
                sql = (
                    f"SELECT r.*, {textsearch.score(dialect, index='memory_fts', alias='r')} AS score FROM {source} "
                    f"WHERE {match} AND {' AND '.join(clauses)} ORDER BY score DESC, r.seq DESC"
                )
                params = [*match_params, *params]
                method = "fulltext"
            else:
                # No words to match: uniform score, most recent first.
                sql = (
                    "SELECT r.*, 1.0 AS score FROM memory_records r "
                    f"WHERE {' AND '.join(clauses)} ORDER BY r.created_at DESC, r.seq DESC"
                )
                method = "default"

            rows = await tx.fetchall(sql, *params)
            matches: list[MemoryMatch] = []
            for row in rows:
                record = _record(row)
                if spec.metadata_filter and not all(
                    record.metadata.get(k) == v for k, v in spec.metadata_filter.items()
                ):
                    continue
                if row["score"] < spec.min_score:
                    continue
                matches.append(
                    MemoryMatch(
                        record=record,
                        score=float(row["score"]),
                        rank=len(matches),
                        retrieval_method=method,
                    )
                )
                if len(matches) >= spec.limit:
                    break
            return matches

        return await self._run(op)

    async def touch(self, caller: MemoryNamespace, record_ids: Sequence[str]) -> None:
        if not record_ids:
            return

        async def op(tx: Tx) -> None:
            visible, params = _visible(caller)
            ids, id_params = _in("id", list(record_ids))
            await tx.execute(
                f"UPDATE memory_records SET last_accessed_at = ?, access_count = access_count + 1 WHERE {visible} AND {ids}",
                datetime.now(tz=timezone.utc).isoformat(),
                *params,
                *id_params,
            )

        await self._run(op)

    async def erase(self, within: MemoryNamespace) -> int:
        clauses, params = ["tenant_id = ?"], [within.tenant_id]
        for column, value in (
            ("user_id", within.user_id),
            ("agent_id", within.agent_id),
            ("session_id", within.session_id),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                params.append(value)

        async def op(tx: Tx) -> int:
            erased = await tx.execute(
                f"DELETE FROM memory_records WHERE {' AND '.join(clauses)}", *params
            )
            if erased and (
                compact := textsearch.compact(
                    self._store.database.dialect, index="memory_fts"
                )
            ):
                # The delete trigger only marks index entries deleted; rewriting the index drops the words themselves.
                await tx.execute(compact)
            return erased

        erased = await self._run(op)
        if erased:
            await self._store.database.reclaim()
        return erased


class SessionState:
    """The ``ShortTermMemory`` of a ``Store``: ``store.session_state``."""

    def __init__(self, store: Store) -> None:
        self._store = store

    @property
    def store(self) -> Store:
        return self._store

    async def get_state(self, session_id: str) -> dict[str, Any]:
        async def op(tx: Tx) -> dict[str, Any]:
            row = await tx.fetchone(
                "SELECT state_json FROM session_state WHERE session_id = ?", session_id
            )
            return json.loads(row["state_json"]) if row else {}

        return await self._store.run(op)

    async def set_state(self, session_id: str, state: dict[str, Any]) -> None:
        async def op(tx: Tx) -> None:
            await _write(tx, session_id, state)

        await self._store.run(op)

    async def update_state(self, session_id: str, patch: dict[str, Any]) -> None:
        async def op(tx: Tx) -> None:
            await tx.lock(f"session_state:{session_id}")
            row = await tx.fetchone(
                "SELECT state_json FROM session_state WHERE session_id = ?", session_id
            )
            state = json.loads(row["state_json"]) if row else {}
            state.update(patch)
            await _write(tx, session_id, state)

        await self._store.run(op)

    async def clear(self, session_id: str) -> None:
        async def op(tx: Tx) -> None:
            await tx.execute(
                "DELETE FROM session_state WHERE session_id = ?", session_id
            )

        await self._store.run(op)


async def _write(tx: Tx, session_id: str, state: dict[str, Any]) -> None:
    await tx.execute(
        "INSERT INTO session_state (session_id, state_json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (session_id) DO UPDATE SET state_json = excluded.state_json, updated_at = excluded.updated_at",
        session_id,
        json.dumps(state, default=str),
        time.time(),
    )


__all__ = ["MEMORY_SCHEMA", "Memory", "SessionState"]
