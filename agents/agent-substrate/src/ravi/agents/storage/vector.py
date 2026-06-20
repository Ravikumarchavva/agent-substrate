"""In-memory vector store for local development and tests (L1).

A dependency-free :class:`~ravi.kernel.storage.vector.VectorStore` implementation
that keeps documents in a per-collection dict and ranks them with brute-force
cosine similarity. It mirrors :class:`PgVectorStore`'s contract exactly, so RAG
pipelines can run against it in tests without Postgres/pgvector.

Embeddings: like ``PgVectorStore``, a document must carry an ``embedding``.
When a document has none and an ``embedding_client`` was supplied, the store
computes it from the document's text; otherwise it raises ``ValueError``.

Usage::

    from ravi.agents.storage import InMemoryVectorStore

    store = InMemoryVectorStore(embedding_client=embed)
    await store.add([Document.from_text("hello")], collection="kb")
    hits = await store.search(query_vec, collection="kb", limit=5)
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from ravi.kernel.storage.vector import Document, SearchResult

if TYPE_CHECKING:
    from ravi.kernel.llm import EmbeddingClient


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 if either is zero)."""
    if len(a) != len(b):
        return 0.0
    dot: float = sum(x * y for x, y in zip(a, b))
    norm_a: float = math.sqrt(sum(x * x for x in a))
    norm_b: float = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class InMemoryVectorStore:
    """Dict-backed VectorStore with brute-force cosine search.

    Args:
        embedding_client: Optional embedding provider used to compute a
            document's embedding when it is missing. When ``None`` every
            document must already carry an ``embedding``.
    """

    def __init__(self, embedding_client: EmbeddingClient | None = None) -> None:
        self._embedding: EmbeddingClient | None = embedding_client
        # collection -> {doc_id: Document}
        self._collections: dict[str, dict[str, Document]] = {}

    # ── Helpers ────────────────────────────────────────────────────────────

    def _bucket(self, collection: str) -> dict[str, Document]:
        return self._collections.setdefault(collection, {})

    async def _ensure_embedding(self, doc: Document) -> Document:
        """Return *doc* with an embedding, computing one if needed."""
        if doc.embedding is not None:
            return doc
        if self._embedding is None:
            raise ValueError(
                f"Document {doc.id} is missing an embedding and no embedding_client "
                "was provided to InMemoryVectorStore."
            )
        vector: list[float] = await self._embedding.embed_single(doc.to_text())
        return Document(
            content=doc.content,
            id=doc.id,
            embedding=vector,
            metadata=doc.metadata,
        )

    @staticmethod
    def _matches_filter(doc: Document, filter: dict[str, Any] | None) -> bool:
        if not filter:
            return True
        return all(doc.metadata.get(key) == value for key, value in filter.items())

    # ── Write ──────────────────────────────────────────────────────────────

    async def add(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
    ) -> list[str]:
        bucket = self._bucket(collection)
        ids: list[str] = []
        for doc in documents:
            stored = await self._ensure_embedding(doc)
            bucket.setdefault(stored.id, stored)  # add = insert-if-absent
            ids.append(stored.id)
        return ids

    async def upsert(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
    ) -> list[str]:
        bucket = self._bucket(collection)
        ids: list[str] = []
        for doc in documents:
            stored = await self._ensure_embedding(doc)
            bucket[stored.id] = stored  # upsert = insert-or-replace
            ids.append(stored.id)
        return ids

    async def delete(
        self,
        ids: list[str],
        *,
        collection: str = "default",
    ) -> int:
        bucket = self._collections.get(collection)
        if not bucket:
            return 0
        removed = 0
        for doc_id in ids:
            if bucket.pop(doc_id, None) is not None:
                removed += 1
        return removed

    # ── Read ───────────────────────────────────────────────────────────────

    async def get(
        self,
        ids: list[str],
        *,
        collection: str = "default",
    ) -> list[Document]:
        bucket = self._collections.get(collection, {})
        return [bucket[doc_id] for doc_id in ids if doc_id in bucket]

    async def search(
        self,
        query_embedding: list[float],
        *,
        collection: str = "default",
        limit: int = 5,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        bucket = self._collections.get(collection, {})
        scored: list[SearchResult] = []
        for doc in bucket.values():
            if doc.embedding is None or not self._matches_filter(doc, filter):
                continue
            score = _cosine_similarity(query_embedding, doc.embedding)
            scored.append(
                SearchResult(
                    id=doc.id,
                    content=doc.content,
                    score=score,
                    metadata=doc.metadata,
                )
            )
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:limit]

    # ── Collections ────────────────────────────────────────────────────────

    async def list_collections(self) -> list[str]:
        return list(self._collections.keys())

    async def delete_collection(self, collection: str) -> int:
        bucket = self._collections.pop(collection, None)
        return len(bucket) if bucket else 0


__all__ = ["InMemoryVectorStore"]
