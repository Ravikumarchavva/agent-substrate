"""pgvector-backed vector store for PostgreSQL.

Uses SQLAlchemy 2 async + the ``pgvector`` Python package for the
``Vector`` column type and HNSW indexing.

Usage::

    from ravi.adapters.vector.pgvector_store import PgVectorStore

    store = PgVectorStore(session_factory=get_session_factory(), dimensions=1536)
    await store.ensure_table()
    ids = await store.add(documents, embeddings, collection="kb")
    results = await store.search(query_vec, collection="kb", limit=5)
"""

from __future__ import annotations
from ravi.logger import setup_logging

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import (
    Column,
    DateTime,
    Index,
    MetaData,
    String,
    Text,
    delete,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from pgvector.sqlalchemy import Vector

from ravi.kernel.vector import Document, SearchResult

logger = setup_logging()

# ── ORM Model ─────────────────────────────────────────────────────────────────

metadata_obj = MetaData()


class _Base(DeclarativeBase):
    metadata = metadata_obj


class VectorDocument(_Base):
    """SQLAlchemy ORM model for vector-stored documents."""

    __tablename__ = "vector_documents"

    id = Column(
        UUID(as_uuid=False), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    collection = Column(String(255), nullable=False, index=True)
    text = Column(Text, nullable=False)
    # The embedding column size is set dynamically via __table_args__
    embedding = Column(Vector(), nullable=False)
    metadata_ = Column("metadata", JSONB, nullable=False, default=dict)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "ix_vector_documents_embedding_cosine",
            embedding,
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


# ── PgVectorStore ─────────────────────────────────────────────────────────────


class PgVectorStore:
    """PostgreSQL + pgvector vector store.

    Creates the ``vector_documents`` table (and HNSW index) on first use
    via ``ensure_table()``.

    Args:
        session_factory: SQLAlchemy ``async_sessionmaker`` bound to the DB.
        dimensions: Embedding vector dimensions (for table/index DDL).
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        dimensions: int = 1536,
    ) -> None:
        self._session_factory = session_factory
        self._dimensions = dimensions
        self._table_ensured = False

    async def ensure_table(self) -> None:
        """Create the vector_documents table and HNSW index if they don't exist."""
        if self._table_ensured:
            return

        async with self._session_factory() as session:
            conn = await session.connection()
            # Ensure pgvector extension
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            # Create table + indexes
            await conn.run_sync(metadata_obj.create_all)
            await session.commit()

        self._table_ensured = True
        logger.info("vector_documents table ensured (dimensions=%d)", self._dimensions)

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

        ids: list[str] = []
        async with self._session_factory() as session:
            for doc, emb in zip(documents, embeddings):
                row = VectorDocument(
                    id=doc.id,
                    collection=collection,
                    text=doc.text,
                    embedding=emb,
                    metadata_=doc.metadata,
                )
                session.add(row)
                ids.append(doc.id)
            await session.commit()

        return ids

    async def search(
        self,
        query_embedding: list[float],
        *,
        collection: str = "default",
        limit: int = 5,
        filter: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        await self.ensure_table()

        # Cosine distance: <=> returns distance (0 = identical, 2 = opposite)
        # Convert to similarity: 1 - distance
        distance_expr = VectorDocument.embedding.cosine_distance(query_embedding)

        stmt = (
            select(
                VectorDocument.id,
                VectorDocument.text,
                VectorDocument.metadata_,
                (1 - distance_expr).label("similarity"),
            )
            .where(VectorDocument.collection == collection)
            .order_by(distance_expr)
            .limit(limit)
        )

        # Apply metadata JSONB filter if provided
        if filter:
            for key, value in filter.items():
                stmt = stmt.where(VectorDocument.metadata_[key].astext == str(value))

        async with self._session_factory() as session:
            result = await session.execute(stmt)
            rows = result.all()

        return [
            SearchResult(
                id=str(row.id),
                text=row.text,
                score=float(row.similarity),
                metadata=row.metadata_ or {},
            )
            for row in rows
        ]

    async def delete(
        self,
        ids: list[str],
        *,
        collection: str = "default",
    ) -> int:
        await self.ensure_table()

        async with self._session_factory() as session:
            stmt = delete(VectorDocument).where(
                VectorDocument.id.in_(ids),
                VectorDocument.collection == collection,
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount  # type: ignore[return-value]

    async def list_collections(self) -> list[str]:
        await self.ensure_table()

        async with self._session_factory() as session:
            stmt = select(VectorDocument.collection).distinct()
            result = await session.execute(stmt)
            return [row[0] for row in result.all()]

    async def delete_collection(self, collection: str) -> int:
        await self.ensure_table()

        async with self._session_factory() as session:
            stmt = delete(VectorDocument).where(
                VectorDocument.collection == collection,
            )
            result = await session.execute(stmt)
            await session.commit()
            return result.rowcount  # type: ignore[return-value]
