"""LanceLongTermMemory — Lance-backed LongTermMemory, no embeddings required.

Sibling to ``DurableMemoryStore`` (Postgres full-text) — same ``LongTermMemory``
Protocol, different backend. Exists specifically so
``PageIndexRAGPipeline`` (``capabilities/knowledge/page_pipeline.py``) can
persist its per-collection outline trees as Lance rows under the per-user
session-document index (``tenants/<tid>/users/<uid>/index/``) instead of
either an in-memory dict (lost on restart) or the *shared* Postgres
``LongTermMemory`` used elsewhere for cross-session user-preference facts —
a genuinely different data lifecycle that shouldn't share a table.

Deliberately no vector column, no embedding step, and no full-text ranking
of ``query`` either: every real caller today (``PageIndexRAGPipeline``)
already knows exactly which ``namespace``/``agent_id`` it wants and
post-filters by its own metadata (see that module's
``_get_collection_tree``) — ``search()`` here is a namespace/agent filter
returning the most recent ``limit`` rows, not a claim of relevance ranking.
If a future caller genuinely needs ``query`` to affect results, that's real
new work (an FTS index + `nearest_to_text`, same mechanism
``LanceDBVectorStore.hybrid_search`` already uses), not something to fake
here.

Same dual connection mode as ``LanceDBVectorStore``
(``capabilities/vector/lancedb_store.py`` — see its module docstring for
what was verified about the namespace/remote mode): a local embedded
directory, or a Lance Namespace REST catalog.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from substrate.kernel.core.identity import Actor
from substrate.kernel.storage.memory import Memory


class LanceLongTermMemory:
    """``LongTermMemory`` backed by a Lance table — one table per
    ``collection`` argument (default ``"memories"``), local file or remote
    Lance Namespace catalog. See module docstring for the connection modes.
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
                "LanceLongTermMemory needs exactly one of `path` or `namespace_uri`"
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

    @staticmethod
    def _agent_key(agent_id: Actor) -> tuple[str, str]:
        return (agent_id.role.value, agent_id.id)

    async def save(
        self,
        agent_id: Actor,
        content: str,
        *,
        namespace: str = "default",
        metadata: dict[str, Any] | None = None,
        ttl_seconds: int | None = None,
    ) -> str:
        # ttl_seconds accepted for Protocol compatibility, not enforced here
        # — no caller passes it today (PageIndexRAGPipeline never does), and
        # a real TTL sweep would need a background reaper this store has no
        # process to run; add one if a real caller needs it.
        db = await self._connection()
        table = await self._open_or_create_table(db)
        agent_type, agent_key = self._agent_key(agent_id)
        memory_id = uuid.uuid4().hex
        await table.add(
            [
                {
                    "id": memory_id,
                    "agent_type": agent_type,
                    "agent_key": agent_key,
                    "namespace": namespace,
                    "content": content,
                    "metadata_json": json.dumps(metadata or {}),
                    "created_at": time.time(),
                }
            ]
        )
        return memory_id

    async def search(
        self,
        agent_id: Actor,
        query: str,
        *,
        namespace: str = "default",
        limit: int = 10,
    ) -> list[Memory]:
        db = await self._connection()
        if self._table_name not in await self._table_names(db):
            return []
        table = await self._open_or_create_table(db)
        agent_type, agent_key = self._agent_key(agent_id)
        rows = (
            await table.query()
            .where(
                f"agent_type = '{agent_type}' AND agent_key = '{agent_key}' "
                f"AND namespace = '{namespace}'"
            )
            .to_list()
        )
        rows.sort(key=lambda r: r["created_at"], reverse=True)
        return [_row_to_memory(r) for r in rows[:limit]]

    async def get(
        self,
        agent_id: Actor,
        memory_id: str,
        *,
        namespace: str = "default",
    ) -> Memory | None:
        db = await self._connection()
        if self._table_name not in await self._table_names(db):
            return None
        table = await self._open_or_create_table(db)
        rows = await table.query().where(f"id = '{memory_id}'").to_list()
        return _row_to_memory(rows[0]) if rows else None

    async def delete(
        self,
        agent_id: Actor,
        memory_id: str,
        *,
        namespace: str = "default",
    ) -> bool:
        db = await self._connection()
        if self._table_name not in await self._table_names(db):
            return False
        table = await self._open_or_create_table(db)
        before = await table.count_rows()
        await table.delete(f"id = '{memory_id}'")
        after = await table.count_rows()
        return after < before

    async def clear(self, agent_id: Actor, *, namespace: str = "default") -> None:
        db = await self._connection()
        if self._table_name not in await self._table_names(db):
            return
        table = await self._open_or_create_table(db)
        agent_type, agent_key = self._agent_key(agent_id)
        await table.delete(
            f"agent_type = '{agent_type}' AND agent_key = '{agent_key}' "
            f"AND namespace = '{namespace}'"
        )


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


def _row_to_memory(row: dict[str, Any]) -> Memory:
    return Memory(
        id=row["id"],
        content=row["content"],
        metadata=json.loads(row["metadata_json"]),
    )


__all__ = ["LanceLongTermMemory"]
