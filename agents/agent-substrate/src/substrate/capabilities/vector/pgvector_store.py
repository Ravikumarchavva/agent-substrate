"""pgvector-backed vector store for PostgreSQL.

Pure raw-SQL implementation — no SQLAlchemy ORM model, no version-specific
dialect helpers. Uses ``engine.begin()`` for writes and ``engine.connect()``
for reads so DDL and DML both work reliably with asyncpg.

Schema notes
------------
- ``text``         : text repr of the content blocks (for FTS / display).
                     Computed via ``Document.to_text()``.
- ``content_json`` : JSONB column storing the full ``list[ContentBlock]``
                     payload as JSON.  Required on all rows.

Usage::

    from substrate.capabilities.vector.pgvector_store import PgVectorStore

    store = PgVectorStore(session_factory=sf, engine=engine, dimensions=384)
    await store.ensure_table()
    ids = await store.add(documents, embeddings, collection="kb")
    results = await store.search(query_vec, collection="kb", limit=5)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from substrate.kernel.core.content import content_block_from_dict
from substrate.kernel.storage.vector import Document, SearchResult
from substrate.logger import setup_logging

logger = setup_logging()


def _blocks_to_json(doc: Document) -> str:
    """Serialize content blocks to a JSON string for the content_json column."""
    return json.dumps([block.model_dump(mode="json") for block in doc.content])


def _blocks_from_json(raw: str | list) -> list:
    """Deserialize blocks from the content_json column."""
    items: list = json.loads(raw) if isinstance(raw, str) else raw
    return [content_block_from_dict(item) for item in items]


_TABLE_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# Rows per multi-row INSERT statement, for both add() and upsert(). Chunked
# for statement size, not the 65535-bind-parameter ceiling (7 params/row
# allows ~9362 rows per statement before hitting that): a 2048-dim halfvec
# renders to a ~30KB text literal, so 100 rows is already a ~3MB SQL
# statement. That captures ~99% of the round-trip reduction (1 -> 100) for
# a fraction of the further gain 250+ would add, at real cost in statement-
# compilation and asyncpg buffer churn.
_INSERT_BATCH_SIZE = 100
_PARAMS_PER_ROW = 7


class PgVectorStore:
    """PostgreSQL + pgvector vector store (raw SQL, no ORM).

    Args:
        session_factory: SQLAlchemy ``async_sessionmaker`` — used for reads.
        engine: The underlying ``AsyncEngine`` — used for DDL and writes.
        dimensions: Embedding vector dimensions (must match the embed model).
        table_name: Table to store documents in. Defaults to
            ``vector_documents``. A second table (different name) is how two
            ``PgVectorStore`` instances with different ``dimensions`` coexist
            — one Postgres column can't hold two vector widths, e.g. the
            local RAG backend's text store (1536, OpenAI) and its image
            store (2048, Qwen3-VL-Embedding-2B) — see ``backends/local.py``.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: Any,
        dimensions: int = 1536,
        table_name: str = "vector_documents",
        insert_batch_size: int = _INSERT_BATCH_SIZE,
    ) -> None:
        if not _TABLE_NAME_RE.match(table_name):
            raise ValueError(
                f"table_name {table_name!r} must match {_TABLE_NAME_RE.pattern!r} "
                "(interpolated directly into SQL — not a user-facing value)."
            )
        self._session_factory = session_factory
        self._engine = engine
        self._dimensions = dimensions
        self._table = table_name
        self._table_ensured = False
        self._insert_batch_size = insert_batch_size
        # pgvector's `vector` type can only be HNSW/IVFFlat-indexed up to
        # 2000 dimensions (real, hit directly: Qwen3-VL-Embedding-2B's
        # 2048-dim output failed CREATE INDEX with "column cannot have more
        # than 2000 dimensions for hnsw index"). `halfvec` (pgvector
        # >=0.7.0, half-precision storage) raises that ceiling to 4000 —
        # used only above 2000 dims so the existing `vector` path (e.g.
        # 1536-dim OpenAI embeddings) is completely unchanged.
        self._vector_type = "halfvec" if dimensions > 2000 else "vector"
        self._vector_ops = (
            "halfvec_cosine_ops" if dimensions > 2000 else "vector_cosine_ops"
        )

    # ── Schema ────────────────────────────────────────────────────────────────

    async def ensure_table(self) -> None:
        """Create this store's table and HNSW index if they don't exist."""
        if self._table_ensured:
            return

        async with self._engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(
                text(f"""
                CREATE TABLE IF NOT EXISTS {self._table} (
                    id          UUID PRIMARY KEY,
                    collection  VARCHAR(255) NOT NULL,
                    text        TEXT NOT NULL,
                    content_json JSONB,
                    embedding   {self._vector_type}({self._dimensions}) NOT NULL,
                    metadata    JSONB NOT NULL DEFAULT '{{}}',
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            )
            await conn.execute(
                text(f"""
                CREATE INDEX IF NOT EXISTS ix_{self._table}_collection
                ON {self._table} (collection)
            """)
            )
            await conn.execute(
                text(f"""
                CREATE INDEX IF NOT EXISTS ix_{self._table}_embedding_cosine
                ON {self._table} USING hnsw (embedding {self._vector_ops})
                WITH (m = 16, ef_construction = 64)
            """)
            )
            # Lexical/term-based retrieval (PostgreSQL full-text search — not
            # literally BM25, `ts_rank` uses a different scoring function)
            # alongside the dense vector column, so hybrid_search() can fuse
            # both signals without a second store. ADD COLUMN IF NOT EXISTS
            # (idempotent) so this also migrates a table created before this
            # column existed, not just fresh ones. Mirrors the exact GENERATED
            # ... STORED + GIN pattern already proven in this codebase for
            # long-term memory (capabilities/memory/durable_memory_store.py).
            await conn.execute(
                text(f"""
                ALTER TABLE {self._table} ADD COLUMN IF NOT EXISTS search_vec tsvector
                GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
            """)
            )
            await conn.execute(
                text(f"""
                CREATE INDEX IF NOT EXISTS ix_{self._table}_search_vec
                ON {self._table} USING gin (search_vec)
            """)
            )

        self._table_ensured = True
        logger.info("%s table ensured (dimensions=%d)", self._table, self._dimensions)

    # ── Write ─────────────────────────────────────────────────────────────────

    def _row_params(
        self, doc: Document, collection: str, now: datetime
    ) -> dict[str, Any]:
        doc_id = doc.id or str(uuid.uuid4())
        if doc.embedding is None:
            raise ValueError(
                f"Document {doc_id} is missing embedding required by PgVectorStore"
            )
        return {
            "id": doc_id,
            "collection": collection,
            "text": doc.to_text(),
            "content_json": _blocks_to_json(doc),
            "embedding": "[" + ",".join(str(x) for x in doc.embedding) + "]",
            "metadata": json.dumps(doc.metadata),
            "created_at": now,
        }

    async def _insert_rows(
        self, conn: Any, rows: list[dict[str, Any]], *, on_conflict_sql: str
    ) -> None:
        """Multi-row ``INSERT ... VALUES (...), (...), ...``, chunked at
        ``self._insert_batch_size`` rows per statement — shared by ``add``
        and ``upsert``, which differ only in their ``ON CONFLICT`` clause.

        Replaces a previous per-row ``await conn.execute`` loop that a
        comment here used to justify as "asyncpg pipelines them on the
        wire" — it doesn't; SQLAlchemy's asyncpg dialect awaits each
        ``execute`` as its own round trip, so that loop was one round trip
        per row. The ``CAST(:embedding AS {vector_type})`` trick (kept
        verbatim per row below) is exactly what makes a multi-row
        ``VALUES`` list work with a typed vector column — the reason this
        wasn't done originally (asyncpg can't infer the type in
        ``executemany``) never actually applied, since this uses an
        explicit ``VALUES`` list, not ``executemany``.
        """
        if not rows:
            return
        assert self._insert_batch_size * _PARAMS_PER_ROW < 65535, (
            f"insert_batch_size={self._insert_batch_size} would exceed "
            "Postgres's 65535 bind-parameter limit per statement"
        )
        start = time.monotonic()
        for chunk_start in range(0, len(rows), self._insert_batch_size):
            chunk = rows[chunk_start : chunk_start + self._insert_batch_size]
            values_sql = ", ".join(
                f"(:id_{i}, :collection_{i}, :text_{i}, CAST(:content_json_{i} AS jsonb), "
                f"CAST(:embedding_{i} AS {self._vector_type}), CAST(:metadata_{i} AS jsonb), :created_at_{i})"
                for i in range(len(chunk))
            )
            params: dict[str, Any] = {}
            for i, row in enumerate(chunk):
                for key, value in row.items():
                    params[f"{key}_{i}"] = value
            await conn.execute(
                text(f"""
                    INSERT INTO {self._table}
                        (id, collection, text, content_json, embedding, metadata, created_at)
                    VALUES {values_sql}
                    {on_conflict_sql}
                """),
                params,
            )
        elapsed = time.monotonic() - start
        logger.info(
            "%s: inserted %d rows in %.2fs (%.0f rows/s)",
            self._table,
            len(rows),
            elapsed,
            len(rows) / elapsed if elapsed > 0 else float("inf"),
        )

    async def add(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
    ) -> list[str]:
        await self.ensure_table()

        now = datetime.now(timezone.utc)
        rows = await asyncio.to_thread(
            lambda: [self._row_params(doc, collection, now) for doc in documents]
        )

        async with self._engine.begin() as conn:
            await self._insert_rows(
                conn, rows, on_conflict_sql="ON CONFLICT (id) DO NOTHING"
            )

        return [row["id"] for row in rows]

    async def get(
        self,
        ids: list[str],
        *,
        collection: str = "default",
    ) -> list[Document]:
        await self.ensure_table()
        if not ids:
            return []

        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(f"""
                    SELECT id, text, content_json, embedding, metadata
                    FROM {self._table}
                    WHERE id = ANY(CAST(:ids AS UUID[])) AND collection = :collection
                """),
                {"ids": ids, "collection": collection},
            )
            rows = result.all()

        documents: list[Document] = []
        for row in rows:
            if row.embedding is None:
                emb = None
            elif isinstance(row.embedding, str):
                emb = [
                    float(x) for x in row.embedding.strip("[]").split(",") if x.strip()
                ]
            elif hasattr(row.embedding, "__iter__"):
                emb = [float(x) for x in row.embedding]
            else:
                emb = None

            documents.append(
                Document(
                    id=str(row.id),
                    content=_blocks_from_json(row.content_json),
                    embedding=emb,
                    metadata=row.metadata or {},
                )
            )
        return documents

    async def upsert(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
    ) -> list[str]:
        await self.ensure_table()
        now = datetime.now(timezone.utc)
        rows = await asyncio.to_thread(
            lambda: [self._row_params(doc, collection, now) for doc in documents]
        )

        # Dedupe by id (last write wins) only for what actually gets sent to
        # Postgres -- ON CONFLICT DO UPDATE aborts the whole statement with
        # "cannot affect row a second time" if one VALUES list repeats an
        # id. Duplicate ids across documents passed in one call are rare
        # (ids default to fresh UUIDs) but real enough to guard rather than
        # assume away. The returned `ids` list still has one entry per
        # input document, in order, unaffected by the dedup.
        deduped: dict[str, dict[str, Any]] = {row["id"]: row for row in rows}

        async with self._engine.begin() as conn:
            await self._insert_rows(
                conn,
                list(deduped.values()),
                on_conflict_sql="""
                    ON CONFLICT (id) DO UPDATE SET
                        text = EXCLUDED.text,
                        content_json = EXCLUDED.content_json,
                        embedding = EXCLUDED.embedding,
                        metadata = EXCLUDED.metadata,
                        created_at = EXCLUDED.created_at
                """,
            )

        return [row["id"] for row in rows]

    async def delete(
        self,
        ids: list[str],
        *,
        collection: str = "default",
    ) -> int:
        await self.ensure_table()

        async with self._engine.begin() as conn:
            result = await conn.execute(
                text(f"""
                    DELETE FROM {self._table}
                    WHERE id = ANY(CAST(:ids AS UUID[])) AND collection = :collection
                """),
                {"ids": ids, "collection": collection},
            )
            return result.rowcount

    async def delete_collection(self, collection: str) -> int:
        await self.ensure_table()

        async with self._engine.begin() as conn:
            result = await conn.execute(
                text(f"DELETE FROM {self._table} WHERE collection = :collection"),
                {"collection": collection},
            )
            return result.rowcount

    async def rename_collection(self, old: str, new: str) -> int:
        """Re-key every row from collection *old* to *new* — a cheap
        re-labeling, no re-embedding. Used to "promote" a staged document
        (ingested under a temporary collection at upload time) into a
        thread's real collection once the user actually sends it. Safe even
        if *new* already has rows: ``id`` is a globally unique UUID
        regardless of collection, so no collision is possible."""
        await self.ensure_table()

        async with self._engine.begin() as conn:
            result = await conn.execute(
                text(
                    f"UPDATE {self._table} SET collection = :new WHERE collection = :old"
                ),
                {"new": new, "old": old},
            )
            return result.rowcount

    # ── Read ──────────────────────────────────────────────────────────────────

    @staticmethod
    def _filter_clauses(
        filter: Optional[dict[str, Any]], params: dict[str, Any]
    ) -> list[str]:
        """Build ``metadata->>'key' = :param`` clauses for an optional filter
        dict, mutating *params* in place. Shared by ``search()`` and
        ``hybrid_search()`` so both apply filters identically."""
        clauses: list[str] = []
        if filter:
            for i, (key, value) in enumerate(filter.items()):
                param_key = f"filter_{i}"
                clauses.append(f"metadata->>'{key}' = :{param_key}")
                params[param_key] = str(value)
        return clauses

    async def search(
        self,
        query_embedding: list[float],
        *,
        collection: str = "default",
        limit: int = 5,
        filter: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        await self.ensure_table()

        emb_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        where_clauses = ["collection = :collection"]
        params: dict[str, Any] = {
            "collection": collection,
            "embedding": emb_str,
            "limit": limit,
        }
        where_clauses += self._filter_clauses(filter, params)

        where_sql = " AND ".join(where_clauses)

        sql = text(f"""
            SELECT id, text, content_json, metadata,
                   1 - (embedding <=> CAST(:embedding AS {self._vector_type})) AS similarity
            FROM {self._table}
            WHERE {where_sql}
            ORDER BY embedding <=> CAST(:embedding AS {self._vector_type})
            LIMIT :limit
        """)

        async with self._engine.connect() as conn:
            result = await conn.execute(sql, params)
            rows = result.all()

        return [
            SearchResult(
                id=str(row.id),
                content=_blocks_from_json(row.content_json),
                score=float(row.similarity),
                metadata=row.metadata or {},
            )
            for row in rows
        ]

    async def lexical_search(
        self,
        query_text: str,
        *,
        collection: str = "default",
        limit: int = 5,
        filter: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        """Lexical-only (full-text) search via ``ts_rank`` — no dense/RRF
        component. Exists mainly so the eval harness (``tests/eval/``) can
        measure Lexical Recall@K standalone, separate from the fused
        ``hybrid_search()`` number; ``score`` here is a raw ``ts_rank``
        value, not comparable across queries the way the RRF score is."""
        await self.ensure_table()

        where_clauses = [
            "collection = :collection",
            "search_vec @@ plainto_tsquery('english', :query_text)",
        ]
        params: dict[str, Any] = {
            "collection": collection,
            "query_text": query_text,
            "limit": limit,
        }
        where_clauses += self._filter_clauses(filter, params)
        where_sql = " AND ".join(where_clauses)

        sql = text(f"""
            SELECT id, text, content_json, metadata,
                   ts_rank(search_vec, plainto_tsquery('english', :query_text)) AS rank
            FROM {self._table}
            WHERE {where_sql}
            ORDER BY rank DESC
            LIMIT :limit
        """)

        async with self._engine.connect() as conn:
            result = await conn.execute(sql, params)
            rows = result.all()

        return [
            SearchResult(
                id=str(row.id),
                content=_blocks_from_json(row.content_json),
                score=float(row.rank),
                metadata=row.metadata or {},
            )
            for row in rows
        ]

    async def hybrid_search(
        self,
        query_embedding: list[float],
        query_text: str,
        *,
        collection: str = "default",
        dense_k: int = 50,
        lexical_k: int = 50,
        fused_k: int = 50,
        rrf_k: int = 60,
        filter: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        """Dense vector search + lexical (full-text) search, fused with
        Reciprocal Rank Fusion — ``score = sum(1 / (rrf_k + rank))`` across
        whichever of the two ranked lists a row appears in (0 contribution
        from a list it's absent from). ``rrf_k=60`` is the standard constant
        used by Elastic/Weaviate/Vespa's default hybrid search — no score
        calibration between cosine similarity and ``ts_rank`` needed, since
        RRF only ever looks at rank position, not the raw scores themselves.

        ``score`` on the returned ``SearchResult`` is the RRF score (not a
        cosine similarity or ts_rank value) — comparable across results from
        this method, not comparable to a plain ``search()`` call's scores.
        """
        await self.ensure_table()

        emb_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

        params: dict[str, Any] = {
            "collection": collection,
            "embedding": emb_str,
            "query_text": query_text,
            "dense_k": dense_k,
            "lexical_k": lexical_k,
            "fused_k": fused_k,
            "rrf_k": rrf_k,
        }
        filter_clauses = self._filter_clauses(filter, params)
        filter_sql = ("AND " + " AND ".join(filter_clauses)) if filter_clauses else ""

        sql = text(f"""
            WITH dense AS (
                SELECT id, ROW_NUMBER() OVER (ORDER BY embedding <=> CAST(:embedding AS {self._vector_type})) AS rank
                FROM {self._table}
                WHERE collection = :collection {filter_sql}
                ORDER BY embedding <=> CAST(:embedding AS {self._vector_type})
                LIMIT :dense_k
            ),
            lexical AS (
                SELECT id, ROW_NUMBER() OVER (
                    ORDER BY ts_rank(search_vec, plainto_tsquery('english', :query_text)) DESC
                ) AS rank
                FROM {self._table}
                WHERE collection = :collection {filter_sql}
                  AND search_vec @@ plainto_tsquery('english', :query_text)
                ORDER BY ts_rank(search_vec, plainto_tsquery('english', :query_text)) DESC
                LIMIT :lexical_k
            )
            SELECT t.id, t.text, t.content_json, t.metadata,
                   COALESCE(1.0 / (:rrf_k + d.rank), 0.0)
                 + COALESCE(1.0 / (:rrf_k + l.rank), 0.0) AS rrf_score
            FROM {self._table} t
            LEFT JOIN dense d ON d.id = t.id
            LEFT JOIN lexical l ON l.id = t.id
            WHERE d.id IS NOT NULL OR l.id IS NOT NULL
            ORDER BY rrf_score DESC
            LIMIT :fused_k
        """)

        async with self._engine.connect() as conn:
            result = await conn.execute(sql, params)
            rows = result.all()

        return [
            SearchResult(
                id=str(row.id),
                content=_blocks_from_json(row.content_json),
                score=float(row.rrf_score),
                metadata=row.metadata or {},
            )
            for row in rows
        ]

    async def list_collections(self) -> list[str]:
        await self.ensure_table()

        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(
                    f"SELECT DISTINCT collection FROM {self._table} ORDER BY collection"
                )
            )
            return [row[0] for row in result.all()]
