"""pgvector-backed vector store for PostgreSQL.

Pure raw-SQL implementation — no SQLAlchemy ORM model, no version-specific
dialect helpers. Uses ``engine.begin()`` for writes and ``engine.connect()``
for reads so DDL and DML both work reliably with asyncpg.

Usage::

    from ravi.capabilities.vector.pgvector_store import PgVectorStore

    store = PgVectorStore(session_factory=sf, engine=engine, dimensions=384)
    await store.ensure_table()
    ids = await store.add(documents, embeddings, collection="kb")
    results = await store.search(query_vec, collection="kb", limit=5)
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ravi.kernel.vector import Document, SearchResult
from ravi.logger import setup_logging

logger = setup_logging()


class PgVectorStore:
    """PostgreSQL + pgvector vector store (raw SQL, no ORM).

    Args:
        session_factory: SQLAlchemy ``async_sessionmaker`` — used for reads.
        engine: The underlying ``AsyncEngine`` — used for DDL and writes.
        dimensions: Embedding vector dimensions (must match the embed model).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        engine: Any,
        dimensions: int = 1536,
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine
        self._dimensions = dimensions
        self._table_ensured = False

    # ── Schema ────────────────────────────────────────────────────────────────

    async def ensure_table(self) -> None:
        """Create the vector_documents table and HNSW index if they don't exist."""
        if self._table_ensured:
            return

        async with self._engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.execute(
                text(f"""
                CREATE TABLE IF NOT EXISTS vector_documents (
                    id          UUID PRIMARY KEY,
                    collection  VARCHAR(255) NOT NULL,
                    text        TEXT NOT NULL,
                    embedding   vector({self._dimensions}) NOT NULL,
                    metadata    JSONB NOT NULL DEFAULT '{{}}',
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            )
            await conn.execute(
                text("""
                CREATE INDEX IF NOT EXISTS ix_vector_documents_collection
                ON vector_documents (collection)
            """)
            )
            await conn.execute(
                text("""
                CREATE INDEX IF NOT EXISTS ix_vector_documents_embedding_cosine
                ON vector_documents USING hnsw (embedding vector_cosine_ops)
                WITH (m = 16, ef_construction = 64)
            """)
            )

        self._table_ensured = True
        logger.info("vector_documents table ensured (dimensions=%d)", self._dimensions)

    # ── Write ─────────────────────────────────────────────────────────────────

    async def add(
        self,
        documents: list[Document],
        embeddings: list[list[float]],
        *,
        collection: str = "default",
    ) -> list[str]:
        await self.ensure_table()

        if len(documents) != len(embeddings):
            raise ValueError(
                f"documents ({len(documents)}) and embeddings ({len(embeddings)}) "
                "must have the same length"
            )

        now = datetime.now(timezone.utc)
        ids: list[str] = []

        # asyncpg cannot infer the vector type in executemany, so we use a single
        # transaction with per-row execute calls. asyncpg pipelines them on the wire
        # so latency is amortised across the batch despite the loop.
        async with self._engine.begin() as conn:
            for doc, emb in zip(documents, embeddings):
                doc_id = doc.id or str(uuid.uuid4())
                await conn.execute(
                    text("""
                        INSERT INTO vector_documents
                            (id, collection, text, embedding, metadata, created_at)
                        VALUES
                            (:id, :collection, :text, CAST(:embedding AS vector), CAST(:metadata AS jsonb), :created_at)
                        ON CONFLICT (id) DO NOTHING
                    """),
                    {
                        "id": doc_id,
                        "collection": collection,
                        "text": doc.text,
                        "embedding": "[" + ",".join(str(x) for x in emb) + "]",
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
                text("""
                    DELETE FROM vector_documents
                    WHERE id = ANY(:ids::uuid[]) AND collection = :collection
                """),
                {"ids": ids, "collection": collection},
            )
            return result.rowcount

    async def delete_collection(self, collection: str) -> int:
        await self.ensure_table()

        async with self._engine.begin() as conn:
            result = await conn.execute(
                text("DELETE FROM vector_documents WHERE collection = :collection"),
                {"collection": collection},
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
            SELECT id, text, metadata,
                   1 - (embedding <=> CAST(:embedding AS vector)) AS similarity
            FROM vector_documents
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
                text=row.text,
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
                    "SELECT DISTINCT collection FROM vector_documents ORDER BY collection"
                )
            )
            return [row[0] for row in result.all()]
