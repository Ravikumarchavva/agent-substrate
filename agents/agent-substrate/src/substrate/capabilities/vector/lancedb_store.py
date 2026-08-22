"""LanceDB-backed vector store — embedded, file-based (a local directory of
Lance datasets), no server, for local dev and small/medium corpora.

Same ``VectorStore`` Protocol as ``PgVectorStore`` (kernel/storage/vector.py),
so a pipeline written against this can switch to Postgres later with zero
code changes beyond the constructor call. Unlike ``InMemoryVectorStore``
(agents/storage/vector.py), state survives process restarts. Unlike a
hand-rolled SQLite table with Python-side cosine similarity, LanceDB does
real vector search (via its own columnar/Arrow storage) — a purpose-built
tool, not a reinvented one.

One ``collection`` == one LanceDB table (its native grouping unit) — cleaner
than cramming a ``collection`` column into a single shared table the way
``PgVectorStore`` does, and it maps ``list_collections``/``delete_collection``/
``rename_collection`` directly onto LanceDB's own table operations.

No index is created (``create_index()`` is never called) — LanceDB does an
exhaustive/exact search by default without one, which is correct and simple
at dev scale; a genuinely large corpus should add an ANN index (or just use
``PgVectorStore``, which already has one).

Metadata ``filter`` is applied in Python after an exhaustive vector search
(same approach ``InMemoryVectorStore`` uses) rather than pushed into a
LanceDB ``where()`` SQL expression — metadata is stored as an opaque JSON
string column (documents carry arbitrary metadata shapes), and building a
real SQL predicate from a generic ``dict`` isn't a clean fit for that. This
is exact and correct (no index means the initial vector search already
covers every row), just not as fast as a real predicate pushdown would be.

Usage::

    from substrate.capabilities.vector.lancedb_store import LanceDBVectorStore

    store = LanceDBVectorStore("data/lancedb")
    ids = await store.add(documents, collection="brochure")
    results = await store.search(query_embedding, collection="brochure", limit=5)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from substrate.kernel.core.content import content_block_from_dict
from substrate.kernel.storage.vector import Document, SearchResult

# Exhaustive search covers every row regardless of this value (no index is
# ever created) — it just needs to be >= the table size so post-filtering
# in Python doesn't silently drop matches that fell outside a too-small
# LanceDB-level limit.
_FETCH_CAP = 100_000


def _blocks_to_json(doc: Document) -> str:
    return json.dumps([block.model_dump(mode="json") for block in doc.content])


def _blocks_from_json(raw: str) -> list:
    return [content_block_from_dict(item) for item in json.loads(raw)]


class LanceDBVectorStore:
    """File-based ``VectorStore`` backed by LanceDB — one table per collection.

    Args:
        path: Directory for the LanceDB database. Created on first use if
            it doesn't exist.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        self._db = None  # lazy: lancedb.connect_async is itself a coroutine

    async def _connection(self):
        if self._db is None:
            import lancedb

            self._db = await lancedb.connect_async(self._path)
        return self._db

    async def _table_names(self, db) -> list[str]:
        # list_tables() returns a paginated ListTablesResponse, not a plain
        # list — .tables is the actual name list, and .page_token is set
        # when there are more pages to fetch.
        names: list[str] = []
        page_token = None
        while True:
            resp = await db.list_tables(page_token=page_token)
            names.extend(resp.tables)
            if not resp.page_token:
                break
            page_token = resp.page_token
        return names

    async def _open_or_create(self, collection: str, rows: list[dict]):
        db = await self._connection()
        if collection in await self._table_names(db):
            table = await db.open_table(collection)
            await table.add(rows)
        else:
            table = await db.create_table(collection, data=rows)
        return table

    # ── Write ────────────────────────────────────────────────────────────

    async def add(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
    ) -> list[str]:
        rows = []
        for doc in documents:
            if doc.embedding is None:
                raise ValueError(
                    f"Document {doc.id!r} has no embedding — "
                    "LanceDBVectorStore does not compute embeddings itself."
                )
            rows.append(
                {
                    "id": doc.id,
                    "vector": doc.embedding,
                    "content_json": _blocks_to_json(doc),
                    "metadata_json": json.dumps(doc.metadata),
                }
            )
        await self._open_or_create(collection, rows)
        return [doc.id for doc in documents]

    async def upsert(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
    ) -> list[str]:
        # LanceDB's own upsert primitive is merge_insert; simplest correct
        # approach here (dev-scale data) is delete-then-add.
        db = await self._connection()
        if collection in await self._table_names(db):
            table = await db.open_table(collection)
            ids = [doc.id for doc in documents]
            if ids:
                id_list = ", ".join(f"'{i}'" for i in ids)
                await table.delete(f"id IN ({id_list})")
        return await self.add(documents, collection=collection)

    # ── Read ─────────────────────────────────────────────────────────────

    async def search(
        self,
        query_embedding: list[float],
        *,
        collection: str = "default",
        limit: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        db = await self._connection()
        if collection not in await self._table_names(db):
            return []
        table = await db.open_table(collection)
        query = await table.search(query_embedding)
        rows = await query.distance_type("cosine").limit(_FETCH_CAP).to_list()

        scored: list[tuple[float, SearchResult]] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            if filter and not all(metadata.get(k) == v for k, v in filter.items()):
                continue
            score = 1.0 - row["_distance"]
            scored.append(
                (
                    score,
                    SearchResult(
                        id=row["id"],
                        content=_blocks_from_json(row["content_json"]),
                        score=score,
                        metadata=metadata,
                    ),
                )
            )
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [result for _, result in scored[:limit]]

    async def get(
        self,
        ids: list[str],
        *,
        collection: str = "default",
    ) -> list[Document]:
        db = await self._connection()
        if not ids or collection not in await self._table_names(db):
            return []
        table = await db.open_table(collection)
        id_list = ", ".join(f"'{i}'" for i in ids)
        rows = await table.query().where(f"id IN ({id_list})").to_list()
        by_id = {
            row["id"]: Document(
                content=_blocks_from_json(row["content_json"]),
                id=row["id"],
                embedding=list(row["vector"]),
                metadata=json.loads(row["metadata_json"]),
            )
            for row in rows
        }
        return [by_id[i] for i in ids if i in by_id]

    async def delete(
        self,
        ids: list[str],
        *,
        collection: str = "default",
    ) -> int:
        db = await self._connection()
        if not ids or collection not in await self._table_names(db):
            return 0
        table = await db.open_table(collection)
        before = await table.count_rows()
        id_list = ", ".join(f"'{i}'" for i in ids)
        await table.delete(f"id IN ({id_list})")
        after = await table.count_rows()
        return before - after

    # ── Collections ──────────────────────────────────────────────────────

    async def list_collections(self) -> list[str]:
        db = await self._connection()
        return await self._table_names(db)

    async def delete_collection(self, collection: str) -> int:
        db = await self._connection()
        if collection not in await self._table_names(db):
            return 0
        table = await db.open_table(collection)
        count = await table.count_rows()
        await db.drop_table(collection)
        return count

    async def rename_collection(self, old: str, new: str) -> int:
        # LanceDB OSS has no native rename_table (cloud-only) — real,
        # confirmed limitation, not assumed. Copy all rows into a new table
        # under `new`, then drop `old`.
        db = await self._connection()
        if old not in await self._table_names(db):
            return 0
        old_table = await db.open_table(old)
        rows = await old_table.query().to_list()
        if rows:
            await db.create_table(new, data=rows, mode="overwrite")
        await db.drop_table(old)
        return len(rows)


__all__ = ["LanceDBVectorStore"]
