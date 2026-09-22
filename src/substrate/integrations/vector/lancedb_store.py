"""LanceDB-backed vector store — embedded, file-based (a local directory of
Lance datasets) *or* a remote Lance Namespace REST catalog (e.g. SeaweedFS's
Lance Catalog — see ``docs/claude_docs``), no server required for the local
mode.

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

No ANN index is created for vector search (``create_index()`` is never
called for it) — LanceDB does an exhaustive/exact search by default without
one, which is correct and simple at dev/per-user scale; a genuinely large
corpus should add one (or just use ``PgVectorStore``, which already has
one). A full-text-search index *is* created lazily on first
``hybrid_search()`` call — see below.

Metadata ``filter`` is applied in Python after an exhaustive vector search
(same approach ``InMemoryVectorStore`` uses) rather than pushed into a
LanceDB ``where()`` SQL expression — metadata is stored as an opaque JSON
string column (documents carry arbitrary metadata shapes), and building a
real SQL predicate from a generic ``dict`` isn't a clean fit for that. This
is exact and correct (no index means the initial vector search already
covers every row), just not as fast as a real predicate pushdown would be.

Hybrid search (``hybrid_search()``, an extra beyond the ``VectorStore``
Protocol — same pattern as ``PgVectorStore.hybrid_search``/``lexical_search``)
uses LanceDB's *native* RRF fusion of dense vector + full-text search, not a
hand-rolled fusion. The one real wrinkle, verified directly (not assumed
from docs): LanceDB's own ``search(query, query_type="hybrid")`` entry point
requires a *registered embedding function* on the table, because it expects
to auto-embed a text query for the vector half itself — that doesn't fit
here (embeddings are computed externally, e.g. via
``capabilities/llm``/the embedding-reranker service). The fix, confirmed
working: build the query via ``table.query().nearest_to(vector).nearest_to_text(text)``
instead of ``table.search(...)`` — chaining those two directly returns an
``AsyncHybridQuery`` without ever consulting the embedding-function
registry.

Remote (namespace) mode connects via ``lancedb.connect_namespace_async("rest", ...)``
to a Lance Namespace REST catalog. Two things verified directly against a
real SeaweedFS Lance Catalog (``weed server -s3.port.lance=9101``), not
assumed from its docs:

* A table identifier is ``(name, namespace_path)`` — a name string plus a
  separate ``list[str]`` namespace path (e.g. ``[bucket, tenant_id,
  user_id]``) — *not* a dotted string. The namespace path must exist
  (``create_namespace(..., mode="exist_ok")``) before a table can be
  created in it.
* The catalog only brokers *metadata* (namespace/table registration,
  atomic-commit coordination); the actual Arrow/Parquet data reads and
  writes go straight to the S3-compatible endpoint, needing its own
  credentials supplied separately from the catalog URI. In this pinned
  lancedb version, the standard ``AWS_*`` environment variables are the
  path confirmed to reach the underlying object-store client end-to-end;
  passing the same values via the ``storage_options`` dict (at connect,
  create_table, and open_table — tried all three) still hit
  ``CredentialsNotLoaded`` in testing, so ``storage_options`` here is best
  read as "supported by the API surface, not yet proven to work" rather
  than a confirmed-working path — set the env vars in the process running
  this instead until that's root-caused. A bucket in this Lance Catalog
  sense is also a provisioned entity (``s3tables.bucket -create -format
  LANCE``), not a free-form key prefix — hence the tenant/user split living
  in the namespace path, not the bucket name.

Usage::

    from substrate.integrations.vector.lancedb_store import LanceDBVectorStore

    # Local/embedded
    store = LanceDBVectorStore(path="data/lancedb")

    # Remote, via a Lance Namespace REST catalog
    store = LanceDBVectorStore(
        namespace_uri="http://seaweedfs:9101",
        namespace_path=["substrate-index", tenant_id, user_id],
    )

    ids = await store.add(documents, collection="brochure")
    results = await store.search(query_embedding, collection="brochure", limit=5)
    results = await store.hybrid_search(query_embedding, "brochure text", collection="brochure")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from substrate.kernel.core.content import content_blocks_to_str, parse_content_block
from substrate.kernel.storage.vector import Document, SearchResult

# Exhaustive search covers every row regardless of this value (no ANN index
# is ever created for vector search) — it just needs to be >= the table
# size so post-filtering in Python doesn't silently drop matches that fell
# outside a too-small LanceDB-level limit.
_FETCH_CAP = 100_000


def _blocks_to_json(doc: Document) -> str:
    return json.dumps([block.model_dump(mode="json") for block in doc.content])


def _blocks_from_json(raw: str) -> list:
    return [parse_content_block(item) for item in json.loads(raw)]


class LanceDBVectorStore:
    """``VectorStore`` backed by LanceDB — one table per collection, local
    file or remote Lance Namespace catalog.

    Args:
        path: Directory for a local, embedded LanceDB database. Mutually
            exclusive with ``namespace_uri``.
        namespace_uri: Base URL of a Lance Namespace REST catalog (e.g.
            SeaweedFS's Lance Catalog). Mutually exclusive with ``path``.
        namespace_path: Required with ``namespace_uri`` — e.g.
            ``[bucket, tenant_id, user_id]``. Created (``exist_ok``) on
            first use.
        storage_options: S3-compatible credentials/endpoint for the actual
            data reads/writes in namespace mode (see module docstring for
            why these are separate from the catalog URI). If omitted, the
            standard ``AWS_*`` environment variables are used instead —
            confirmed to be the path that actually reaches the object-store
            client for the pinned lancedb version.
    """

    def __init__(
        self,
        path: str | Path | None = None,
        *,
        namespace_uri: str | None = None,
        namespace_path: list[str] | None = None,
        storage_options: dict[str, str] | None = None,
    ) -> None:
        if bool(path) == bool(namespace_uri):
            raise ValueError(
                "LanceDBVectorStore needs exactly one of `path` or `namespace_uri`"
            )
        if namespace_uri and not namespace_path:
            raise ValueError("namespace_path is required with namespace_uri")
        self._path = str(path) if path else None
        self._namespace_uri = namespace_uri
        self._namespace_path = namespace_path
        self._storage_options = storage_options
        self._db = None  # lazy: lancedb.connect_async is itself a coroutine
        self._namespace_ready = False
        self._fts_indexed: set[str] = set()

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

    def _ns_kwargs(self) -> dict[str, Any]:
        """namespace_path only — for catalog-metadata-only calls
        (list_tables/drop_table) that take no storage credentials."""
        if not self._namespace_uri:
            return {}
        return {"namespace_path": self._namespace_path}

    def _table_kwargs(self) -> dict[str, Any]:
        """namespace_path + storage credentials — for calls that touch the
        actual table data (create_table/open_table). Empty in local-path
        mode."""
        if not self._namespace_uri:
            return {}
        kwargs: dict[str, Any] = {"namespace_path": self._namespace_path}
        if self._storage_options:
            kwargs["storage_options"] = self._storage_options
        return kwargs

    async def _table_names(self, db) -> list[str]:
        # list_tables() returns a paginated ListTablesResponse, not a plain
        # list — .tables is the actual name list, and .page_token is set
        # when there are more pages to fetch.
        names: list[str] = []
        page_token = None
        while True:
            resp = await db.list_tables(page_token=page_token, **self._ns_kwargs())
            names.extend(resp.tables)
            if not resp.page_token:
                break
            page_token = resp.page_token
        return names

    async def _open_or_create(self, collection: str, rows: list[dict]):
        db = await self._connection()
        if collection in await self._table_names(db):
            table = await db.open_table(collection, **self._table_kwargs())
            await table.add(rows)
        else:
            table = await db.create_table(collection, data=rows, **self._table_kwargs())
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
                    # Plain-text projection of `content`, for FTS/hybrid
                    # search only — `content_json` stays the source of
                    # truth for reconstructing real ContentBlocks.
                    "text": content_blocks_to_str(doc.content),
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
            table = await db.open_table(collection, **self._table_kwargs())
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
        table = await db.open_table(collection, **self._table_kwargs())
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
        table = await db.open_table(collection, **self._table_kwargs())
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
        table = await db.open_table(collection, **self._table_kwargs())
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
        table = await db.open_table(collection, **self._table_kwargs())
        count = await table.count_rows()
        await db.drop_table(collection, **self._ns_kwargs())
        self._fts_indexed.discard(collection)
        return count

    async def rename_collection(self, old: str, new: str) -> int:
        # LanceDB OSS has no native rename_table (cloud-only) — real,
        # confirmed limitation, not assumed. Copy all rows into a new table
        # under `new`, then drop `old`.
        db = await self._connection()
        if old not in await self._table_names(db):
            return 0
        old_table = await db.open_table(old, **self._table_kwargs())
        rows = await old_table.query().to_list()
        if rows:
            await db.create_table(new, data=rows, mode="overwrite", **self._table_kwargs())
        await db.drop_table(old, **self._ns_kwargs())
        self._fts_indexed.discard(old)
        return len(rows)

    # ── Hybrid search (extra, beyond the VectorStore Protocol — same
    # pattern as PgVectorStore.hybrid_search/lexical_search) ───────────────

    async def hybrid_search(
        self,
        query_embedding: list[float],
        query_text: str,
        *,
        collection: str = "default",
        limit: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Dense vector + full-text search, fused with LanceDB's native RRF
        reranker — not a hand-rolled fusion. See the module docstring for
        why this goes through ``table.query().nearest_to(...).nearest_to_text(...)``
        rather than ``table.search(..., query_type="hybrid")`` (the latter
        requires a registered embedding function, which doesn't fit
        externally-computed embeddings)."""
        db = await self._connection()
        if collection not in await self._table_names(db):
            return []
        table = await db.open_table(collection, **self._table_kwargs())
        await self._ensure_fts_index(table, collection)

        from lancedb.rerankers import RRFReranker

        query = table.query().nearest_to(query_embedding).nearest_to_text(query_text)
        query = query.rerank(reranker=RRFReranker())
        rows = await query.limit(_FETCH_CAP).to_list()

        scored: list[tuple[float, SearchResult]] = []
        for row in rows:
            metadata = json.loads(row["metadata_json"])
            if filter and not all(metadata.get(k) == v for k, v in filter.items()):
                continue
            score = row.get("_relevance_score", 0.0)
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

    async def _ensure_fts_index(self, table, collection: str) -> None:
        if collection in self._fts_indexed:
            return
        from lancedb.index import FTS

        existing = await table.list_indices()
        if not any(idx.columns == ["text"] for idx in existing):
            await table.create_index("text", config=FTS())
        self._fts_indexed.add(collection)


__all__ = ["LanceDBVectorStore"]
