"""LanceMemoryStore — Lance-backed MemoryStore, no embeddings required.

Sibling to ``DurableMemoryStore`` (Postgres full-text) — same ``MemoryStore``
Protocol, different backend. Exists specifically so
``PageIndexRAGPipeline`` (``integrations/knowledge/page_pipeline.py``) can
persist its per-collection outline trees as Lance rows under the per-user
session-document index (``tenants/<tid>/users/<uid>/index/``) instead of
either an in-memory dict (lost on restart) or the shared Postgres
``MemoryStore`` used elsewhere for cross-session user-preference facts.

Dual connection mode: a local embedded directory, or a Lance Namespace REST catalog.

A record is addressed by ``(tenant_id, id)``. Lance filters are SQL text with no parameters,
so the only strings ever spliced into one are a tenant and an id, each as an escaped literal;
who may *see* a record is decided in Python against the rows that filter returns.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Sequence

from substrate.kernel.abstractions.core.content import content_blocks_to_str
from substrate.kernel.abstractions.exceptions import ScopeViolationError
from substrate.kernel.abstractions.storage.memory import (
    MemoryMatch,
    MemoryNamespace,
    MemoryQuery,
    MemoryRecord,
)


class LanceMemoryStore:
    """``MemoryStore`` backed by a Lance table — one table per
    ``table_name`` argument (default ``"memories"``), local file or remote
    Lance Namespace catalog.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        namespace_uri: str | None = None,
        namespace_path: list[str] | None = None,
        storage_options: dict[str, str] | None = None,
        table_name: str = "memories",
    ) -> None:
        if bool(path) == bool(namespace_uri):
            raise ValueError(
                "LanceMemoryStore needs exactly one of `path` or `namespace_uri`"
            )
        if namespace_uri and not namespace_path:
            raise ValueError("namespace_path is required with namespace_uri")
        self._path = str(path) if path else None
        self._namespace_uri = namespace_uri
        self._namespace_path = namespace_path
        self._storage_options = storage_options
        self._table_name = table_name
        self._db = None
        self._namespace_ready = False

    async def _connection(self):
        if self._db is None:
            import lancedb

            if self._namespace_uri:
                self._db = lancedb.connect_namespace_async(
                    "rest",
                    {"uri": self._namespace_uri},
                    storage_options=self._storage_options,
                )
            else:
                self._db = await lancedb.connect_async(self._path)
        if self._namespace_uri and not self._namespace_ready:
            await self._db.create_namespace(self._namespace_path, mode="exist_ok")
            self._namespace_ready = True
        return self._db

    def _table_kwargs(self) -> dict[str, Any]:
        if not self._namespace_uri:
            return {}
        kwargs: dict[str, Any] = {"namespace_path": self._namespace_path}
        if self._storage_options:
            kwargs["storage_options"] = self._storage_options
        return kwargs

    async def _table_names(self, db) -> list[str]:
        names: list[str] = []
        page_token = None
        ns_kwargs = {"namespace_path": self._namespace_path} if self._namespace_uri else {}
        while True:
            resp = await db.list_tables(page_token=page_token, **ns_kwargs)
            names.extend(resp.tables)
            if not resp.page_token:
                break
            page_token = resp.page_token
        return names

    async def _open_or_create_table(self, db):
        if self._table_name in await self._table_names(db):
            return await db.open_table(self._table_name, **self._table_kwargs())
        return await db.create_table(
            self._table_name,
            schema=_arrow_schema(),
            **self._table_kwargs(),
        )

    async def _table(self):
        return await self._open_or_create_table(await self._connection())

    async def _existing_table(self):
        db = await self._connection()
        if self._table_name not in await self._table_names(db):
            return None
        return await self._open_or_create_table(db)

    async def save(self, record: MemoryRecord) -> str:
        """Persist or replace a record. Refuses to take over another namespace's record."""
        table = await self._table()
        ns = record.namespace
        existing = await table.query().where(_key(ns.tenant_id, record.id)).to_list()
        if existing and _record_of(existing[0]).namespace != ns:
            raise ScopeViolationError(
                f"record {record.id!r} already belongs to a different namespace in tenant {ns.tenant_id!r}",
                record_id=record.id,
            )
        row = {
            "tenant_id": ns.tenant_id,
            "id": record.id,
            "user_id": ns.user_id,
            "agent_id": ns.agent_id,
            "session_id": ns.session_id,
            "content": content_blocks_to_str(list(record.content)),
            "record_json": record.model_dump_json(),
            "created_at": existing[0]["created_at"] if existing else time.time(),
            "last_accessed_at": existing[0]["last_accessed_at"] if existing else None,
            "access_count": existing[0]["access_count"] if existing else 0,
        }
        # One atomic upsert: a crash cannot leave the record deleted and not yet re-added.
        await (
            table.merge_insert(["tenant_id", "id"])
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute([row])
        )
        return record.id

    async def get(self, caller: MemoryNamespace, record_id: str) -> MemoryRecord | None:
        """The record, if it exists and is visible from ``caller``."""
        table = await self._existing_table()
        if table is None:
            return None
        rows = await table.query().where(_key(caller.tenant_id, record_id)).to_list()
        record = _record_of(rows[0]) if rows else None
        return record if record is not None and record.namespace.visible_from(caller) else None

    async def delete(self, caller: MemoryNamespace, record_id: str) -> bool:
        """Permanently delete a record ``caller`` owns (see ``MemoryNamespace.owned_by``)."""
        record = await self.get(caller, record_id)
        if record is None or not record.namespace.owned_by(caller):
            return False
        table = await self._existing_table()
        assert table is not None
        await table.delete(_key(caller.tenant_id, record_id))
        return True

    async def query(self, spec: MemoryQuery) -> list[MemoryMatch]:
        """Execute structured search conforming to MemoryQuery."""
        table = await self._existing_table()
        if table is None:
            return []
        caller = spec.namespace
        rows = await table.query().where(f"tenant_id = {_lit(caller.tenant_id)}").to_list()
        rows.sort(key=lambda r: r["created_at"], reverse=True)

        matches: list[MemoryMatch] = []
        for r in rows:
            rec = _record_of(r)
            if spec.tenant_wide is None and not rec.namespace.visible_from(caller):
                continue
            if spec.categories and rec.category not in spec.categories:
                continue
            if spec.statuses and rec.status not in spec.statuses:
                continue
            if spec.text_query and spec.text_query.lower() not in rec.to_text().lower():
                continue
            if spec.metadata_filter and not all(rec.metadata.get(k) == v for k, v in spec.metadata_filter.items()):
                continue
            matches.append(MemoryMatch(record=rec, score=1.0, rank=len(matches), retrieval_method="lance"))
            if len(matches) >= spec.limit:
                break
        return matches

    async def touch(self, caller: MemoryNamespace, record_ids: Sequence[str]) -> None:
        """Count an access to each visible record in ``record_ids``."""
        table = await self._existing_table()
        if table is None:
            return
        now = time.time()
        for record_id in record_ids:
            rows = await table.query().where(_key(caller.tenant_id, record_id)).to_list()
            if rows and _record_of(rows[0]).namespace.visible_from(caller):
                await table.update(
                    {"last_accessed_at": now, "access_count": rows[0]["access_count"] + 1},
                    where=_key(caller.tenant_id, record_id),
                )

    async def erase(self, within: MemoryNamespace) -> int:
        """Remove every record under ``within``. Returns how many."""
        table = await self._existing_table()
        if table is None:
            return 0
        rows = await table.query().where(f"tenant_id = {_lit(within.tenant_id)}").to_list()
        doomed = [r["id"] for r in rows if _record_of(r).namespace.within(within)]
        for record_id in doomed:
            await table.delete(_key(within.tenant_id, record_id))
        return len(doomed)


def _lit(value: str) -> str:
    """A SQL string literal for ``value``. Lance filters take no parameters, so this is the
    only way a caller-supplied string reaches one — quotes doubled, NUL refused."""
    if "\x00" in value:
        raise ValueError("NUL is not allowed in a tenant or record id")
    return "'" + value.replace("'", "''") + "'"


def _key(tenant_id: str, record_id: str) -> str:
    return f"tenant_id = {_lit(tenant_id)} AND id = {_lit(record_id)}"


def _arrow_schema():
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("tenant_id", pa.utf8(), nullable=False),
            pa.field("id", pa.utf8(), nullable=False),
            pa.field("user_id", pa.utf8()),
            pa.field("agent_id", pa.utf8()),
            pa.field("session_id", pa.utf8()),
            pa.field("content", pa.utf8()),
            pa.field("record_json", pa.utf8()),
            pa.field("created_at", pa.float64()),
            pa.field("last_accessed_at", pa.float64()),
            pa.field("access_count", pa.int64()),
        ]
    )


def _record_of(row: dict[str, Any]) -> MemoryRecord:
    from datetime import datetime, timezone

    record = MemoryRecord.model_validate_json(row["record_json"])
    accessed = row.get("last_accessed_at")
    return record.model_copy(
        update={
            "access_count": int(row.get("access_count") or 0),
            "last_accessed_at": datetime.fromtimestamp(accessed, tz=timezone.utc) if accessed else None,
        }
    )


__all__ = ["LanceMemoryStore"]
