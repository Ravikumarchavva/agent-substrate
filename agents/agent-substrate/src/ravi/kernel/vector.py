"""Vector store contracts — Protocol and shared value types for RAG."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass(frozen=True)
class Document:
    """A text chunk with optional metadata ready for vector storage.

    ``embedding`` is optional: if provided, the store skips embedding
    (useful when the caller pre-computes embeddings or when the store
    supports server-side embedding and the field is ignored).
    """

    text: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    embedding: Optional[list[float]] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SearchResult:
    """A single result from a vector similarity search."""

    id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class VectorStore(Protocol):
    """Contract every vector store adapter must satisfy."""

    async def add(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
    ) -> list[str]:
        """Persist *documents* and return their assigned ids.

        If ``document.embedding`` is ``None``, the store is responsible for
        computing embeddings (e.g. via a server-side embedding model).
        """
        ...

    async def search(
        self,
        query_embedding: list[float],
        *,
        collection: str = "default",
        limit: int = 5,
        filter: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]: ...

    async def get(
        self,
        ids: list[str],
        *,
        collection: str = "default",
    ) -> list[Document]:
        """Retrieve documents by id."""
        ...

    async def upsert(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
    ) -> list[str]:
        """Insert or replace documents by id."""
        ...

    async def delete(
        self,
        ids: list[str],
        *,
        collection: str = "default",
    ) -> int: ...

    async def list_collections(self) -> list[str]: ...

    async def delete_collection(self, collection: str) -> int: ...


__all__ = ["Document", "SearchResult", "VectorStore"]
