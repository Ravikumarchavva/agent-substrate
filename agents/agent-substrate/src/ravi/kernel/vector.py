"""Vector store contracts — Protocol and shared value types for RAG."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Protocol


@dataclass
class Document:
    """A chunk of text with optional metadata ready for vector storage."""

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
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
        embeddings: list[list[float]],
        *,
        collection: str = "default",
    ) -> list[str]: ...

    async def search(
        self,
        query_embedding: list[float],
        *,
        collection: str = "default",
        limit: int = 5,
        filter: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]: ...

    async def delete(
        self,
        ids: list[str],
        *,
        collection: str = "default",
    ) -> int: ...

    async def list_collections(self) -> list[str]: ...

    async def delete_collection(self, collection: str) -> int: ...


__all__ = ["Document", "SearchResult", "VectorStore"]
