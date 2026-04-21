"""Abstract vector store and shared data types for RAG.

Concrete implementations live in ``integrations/`` (e.g. ``PgVectorStore``).
This module stays in ``core/`` with zero external dependencies.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Document:
    """A chunk of text with optional metadata ready for vector storage.

    Attributes:
        text: The document text content.
        metadata: Arbitrary metadata (source file, page number, etc.).
        id: Unique identifier.  Generated automatically if not provided.
    """

    text: str
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class SearchResult:
    """A single result from a vector similarity search.

    Attributes:
        id: Document identifier.
        text: Original document text.
        score: Similarity score (higher is better; 1.0 = identical).
        metadata: Document metadata.
    """

    id: str
    text: str
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseVectorStore(ABC):
    """Abstract base class for vector stores.

    Implementations must handle persistence, indexing, and similarity search.
    All methods are async.
    """

    @abstractmethod
    async def add(
        self,
        documents: list[Document],
        embeddings: list[list[float]],
        *,
        collection: str = "default",
    ) -> list[str]:
        """Store documents with their embeddings.

        Args:
            documents: Documents to store.
            embeddings: Pre-computed embedding vectors (one per document).
            collection: Namespace for the document set.

        Returns:
            List of document IDs that were stored.
        """
        ...

    @abstractmethod
    async def search(
        self,
        query_embedding: list[float],
        *,
        collection: str = "default",
        limit: int = 5,
        filter: Optional[dict[str, Any]] = None,
    ) -> list[SearchResult]:
        """Search for similar documents.

        Args:
            query_embedding: The query vector.
            collection: Namespace to search within.
            limit: Maximum number of results.
            filter: Optional metadata filter (implementation-specific).

        Returns:
            List of search results ordered by descending similarity.
        """
        ...

    @abstractmethod
    async def delete(
        self,
        ids: list[str],
        *,
        collection: str = "default",
    ) -> int:
        """Delete documents by ID.

        Returns:
            Number of documents actually deleted.
        """
        ...

    @abstractmethod
    async def list_collections(self) -> list[str]:
        """Return all collection names that contain documents."""
        ...

    @abstractmethod
    async def delete_collection(self, collection: str) -> int:
        """Delete all documents in a collection.

        Returns:
            Number of documents deleted.
        """
        ...
