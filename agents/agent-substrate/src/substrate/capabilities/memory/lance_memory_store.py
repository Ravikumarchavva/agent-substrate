"""LanceMemoryStore — Lance-backed MemoryStore, no embeddings required.

Sibling to ``DurableMemoryStore`` (Postgres full-text) — same ``MemoryStore``
Protocol, different backend. Exists specifically so
``PageIndexRAGPipeline`` (``capabilities/knowledge/page_pipeline.py``) can
persist its per-collection outline trees as Lance rows under the per-user
session-document index (``tenants/<tid>/users/<uid>/index/``) instead of
either an in-memory dict (lost on restart) or the shared Postgres
``MemoryStore`` used elsewhere for cross-session user-preference facts.

Dual connection mode: a local embedded directory, or a Lance Namespace REST catalog.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Sequence

from substrate.kernel.core.content import (
    ContentBlock,
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

    async def save(self, record: MemoryRecord) -> str:
        """Persist or replace a record by its ID (explicit upsert semantics)."""
        db = await self._connection()
        table = await self._open_or_create_table(db)

        memory_id = record.id
        agent_type = "user" if record.namespace.user_id else "agent"
        agent_key = record.namespace.user_id or record.namespace.agent_id or "default"
        ns_str = record.namespace.tenant_id or "default"
        blocks = list(record.content)
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

        # Explicit replacement semantics on save: delete existing by ID before appending
        try:
            await table.delete(f"id = '{memory_id}'")
        except Exception:
            pass

        await table.add(
            [
                {
                    "id": memory_id,
                    "agent_type": agent_type,
                    "agent_key": agent_key,
                    "namespace": ns_str,
                    "content": text_content,
                    "metadata_json": json.dumps(meta),
                    "created_at": time.time(),
                }
            ]
        )
        return memory_id

    async def get(self, record_id: str) -> MemoryRecord | None:
        """Retrieve a specific record by ID."""
        db = await self._connection()
        if self._table_name not in await self._table_names(db):
            return None
        table = await self._open_or_create_table(db)

        rows = await table.query().where(f"id = '{record_id}'").to_list()
        return _row_to_memory(rows[0]) if rows else None

    async def delete(self, record_id: str) -> bool:
        """Permanently delete a record by ID. Returns True if deleted."""
        db = await self._connection()
        if self._table_name not in await self._table_names(db):
            return False
        table = await self._open_or_create_table(db)

        before = await table.count_rows()
        await table.delete(f"id = '{record_id}'")
        after = await table.count_rows()
        return after < before

    async def query(self, spec: MemoryQuery) -> list[MemoryMatch]:
        """Execute structured search conforming to MemoryQuery."""
        db = await self._connection()
        if self._table_name not in await self._table_names(db):
            return []
        table = await self._open_or_create_table(db)

        where_clauses = [f"namespace = '{spec.namespace.tenant_id}'"]
        if spec.namespace.user_id:
            where_clauses.append(f"agent_type = 'user' AND agent_key = '{spec.namespace.user_id}'")
        elif spec.namespace.agent_id:
            where_clauses.append(f"agent_type = 'agent' AND agent_key = '{spec.namespace.agent_id}'")

        query_builder = table.query().where(" AND ".join(where_clauses))
        rows = await query_builder.to_list()
        rows.sort(key=lambda r: r["created_at"], reverse=True)

        matches: list[MemoryMatch] = []
        for i, r in enumerate(rows):
            rec = _row_to_memory(r)
            if spec.categories and rec.category not in spec.categories:
                continue
            if spec.statuses and rec.status not in spec.statuses:
                continue
            if spec.text_query and spec.text_query.lower() not in rec.to_text().lower():
                continue
            matches.append(MemoryMatch(record=rec, score=1.0, rank=i, retrieval_method="lance"))
            if len(matches) >= spec.limit:
                break
        return matches

    async def touch(self, record_ids: Sequence[str]) -> None:
        """Update last_accessed_at for records."""
        pass  # In-place partial scalar update not supported by basic Lance format

    async def clear(self, namespace: MemoryNamespace) -> None:
        """Purge all records matching the given namespace boundary."""
        db = await self._connection()
        if self._table_name not in await self._table_names(db):
            return
        table = await self._open_or_create_table(db)

        where_sql = f"namespace = '{namespace.tenant_id}'"
        if namespace.user_id:
            where_sql += f" AND agent_type = 'user' AND agent_key = '{namespace.user_id}'"
        elif namespace.agent_id:
            where_sql += f" AND agent_type = 'agent' AND agent_key = '{namespace.agent_id}'"

        await table.delete(where_sql)


def _arrow_schema():
    import pyarrow as pa

    return pa.schema(
        [
            pa.field("id", pa.utf8()),
            pa.field("agent_type", pa.utf8()),
            pa.field("agent_key", pa.utf8()),
            pa.field("namespace", pa.utf8()),
            pa.field("content", pa.utf8()),
            pa.field("metadata_json", pa.utf8()),
            pa.field("created_at", pa.float64()),
        ]
    )


def _row_to_memory(row: dict[str, Any]) -> MemoryRecord:
    meta = json.loads(row["metadata_json"]) if isinstance(row["metadata_json"], str) else dict(row["metadata_json"])
    blocks_raw = meta.pop("_blocks", None) if isinstance(meta, dict) else None
    if blocks_raw:
        blocks = [parse_content_block(b) for b in blocks_raw]
    else:
        blocks = [TextBlock(text=row["content"])]

    cat_val = meta.pop("_category", MemoryCategory.SEMANTIC.value) if isinstance(meta, dict) else MemoryCategory.SEMANTIC.value
    status_val = meta.pop("_status", MemoryStatus.ACTIVE.value) if isinstance(meta, dict) else MemoryStatus.ACTIVE.value
    ns_raw = meta.pop("_namespace", None) if isinstance(meta, dict) else None
    prov_raw = meta.pop("_provenance", None) if isinstance(meta, dict) else None

    ns = MemoryNamespace(**ns_raw) if ns_raw else MemoryNamespace(tenant_id=row.get("namespace", "default"))
    prov = MemoryProvenance(**prov_raw) if prov_raw else MemoryProvenance()

    return MemoryRecord(
        id=row["id"],
        content=tuple(blocks),
        category=MemoryCategory(cat_val),
        status=MemoryStatus(status_val),
        namespace=ns,
        provenance=prov,
        metadata=meta,
    )


# Alias for transition compatibility
LanceLongTermMemory = LanceMemoryStore

__all__ = ["LanceMemoryStore", "LanceLongTermMemory"]
