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

import json
import re
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
            store (768, SigLIP) — see ``backends/local.py``.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: Any,
        dimensions: int = 1536,
        table_name: str = "vector_documents",
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
                    embedding   vector({self._dimensions}) NOT NULL,
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
                ON {self._table} USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """)
            )

        self._table_ensured = True
        logger.info("%s table ensured (dimensions=%d)", self._table, self._dimensions)

    # ── Write ─────────────────────────────────────────────────────────────────

    async def add(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
    ) -> list[str]:
        await self.ensure_table()

        now = datetime.now(timezone.utc)
        ids: list[str] = []

        # asyncpg cannot infer the vector type in executemany, so we use a single
        # transaction with per-row execute calls. asyncpg pipelines them on the wire
        # so latency is amortised across the batch despite the loop.
        async with self._engine.begin() as conn:
            for doc in documents:
                doc_id = doc.id or str(uuid.uuid4())
                if doc.embedding is None:
                    raise ValueError(
                        f"Document {doc_id} is missing embedding required by PgVectorStore"
                    )

                emb_str = "[" + ",".join(str(x) for x in doc.embedding) + "]"
                await conn.execute(
                    text(f"""
                        INSERT INTO {self._table}
                            (id, collection, text, content_json, embedding, metadata, created_at)
                        VALUES
                            (:id, :collection, :text, CAST(:content_json AS jsonb),
                             CAST(:embedding AS vector), CAST(:metadata AS jsonb), :created_at)
                        ON CONFLICT (id) DO NOTHING
                    """),
                    {
                        "id": doc_id,
                        "collection": collection,
                        "text": doc.to_text(),
                        "content_json": _blocks_to_json(doc),
                        "embedding": emb_str,
                        "metadata": json.dumps(doc.metadata),
                        "created_at": now,
                    },
                )
                ids.append(doc_id)

        return ids

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
        ids: list[str] = []

        async with self._engine.begin() as conn:
            for doc in documents:
                doc_id = doc.id or str(uuid.uuid4())
                if doc.embedding is None:
                    raise ValueError(
                        f"Document {doc_id} is missing embedding required by PgVectorStore"
                    )

                emb_str = "[" + ",".join(str(x) for x in doc.embedding) + "]"
                await conn.execute(
                    text(f"""
                        INSERT INTO {self._table}
                            (id, collection, text, content_json, embedding, metadata, created_at)
                        VALUES
                            (:id, :collection, :text, CAST(:content_json AS jsonb),
                             CAST(:embedding AS vector), CAST(:metadata AS jsonb), :created_at)
                        ON CONFLICT (id) DO UPDATE SET
                            text = EXCLUDED.text,
                            content_json = EXCLUDED.content_json,
                            embedding = EXCLUDED.embedding,
                            metadata = EXCLUDED.metadata,
                            created_at = EXCLUDED.created_at
                    """),
                    {
                        "id": doc_id,
                        "collection": collection,
                        "text": doc.to_text(),
                        "content_json": _blocks_to_json(doc),
                        "embedding": emb_str,
                        "metadata": json.dumps(doc.metadata),
                        "created_at": now,
                    },
                )
                ids.append(doc_id)

        return ids

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

        if filter:
            for i, (key, value) in enumerate(filter.items()):
                param_key = f"filter_{i}"
                where_clauses.append(f"metadata->>'{key}' = :{param_key}")
                params[param_key] = str(value)

        where_sql = " AND ".join(where_clauses)

        sql = text(f"""
            SELECT id, text, content_json, metadata,
                   1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
            FROM {self._table}
            WHERE {where_sql}
            ORDER BY embedding <=> CAST(:embedding AS vector)
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

    async def list_collections(self) -> list[str]:
        await self.ensure_table()

        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(
                    f"SELECT DISTINCT collection FROM {self._table} ORDER BY collection"
                )
            )
            return [row[0] for row in result.all()]
