"""Vector store contracts — Protocol and shared value types for RAG.

A ``Document`` carries *multimodal* content: text, images, audio, video,
structured data, or any combination thereof, expressed as a list of
``ContentBlock`` objects (the same primitive used everywhere else in the
kernel).  Callers that only work with plain text use ``Document.to_text()``
to get a string representation without caring about the underlying modality.

``SearchResult`` mirrors ``Document`` so retrieve operations return the same
rich content that was stored.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from substrate.kernel.core.content import ContentBlock, TextBlock, content_blocks_to_str


@dataclass(frozen=True)
class Document:
    """A content chunk with optional metadata ready for vector storage.

    ``content`` is a list of ``ContentBlock`` — text, images, audio,
    structured data, or any mix.  Use ``Document.from_text(s)`` for the
    common case of plain-text chunks.

    ``embedding`` is optional: if provided, the store skips embedding
    (useful when the caller pre-computes embeddings or when the store
    supports server-side embedding and the field is ignored).
    """

    content: list[ContentBlock] = field(default_factory=list)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    embedding: list[float] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    # ── Convenience constructors ───────────────────────────────────────────

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        id: str | None = None,
        embedding: list[float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "Document":
        """Create a text-only document — the common case for plain-text RAG."""
        return cls(
            content=[TextBlock(text=text)],
            id=id or str(uuid.uuid4()),
            embedding=embedding,
            metadata=metadata or {},
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def to_text(self) -> str:
        """Return a human-readable text representation of the content.

        Suitable for embedding, display, or passing to an LLM as context.
        Each block contributes its own text via ``to_text_repr()``.
        """
        return content_blocks_to_str(self.content)


@dataclass(frozen=True)
class SearchResult:
    """A single result from a vector similarity search.

    ``content`` mirrors ``Document.content`` — the same multimodal blocks
    that were stored are returned unchanged so callers can render, embed,
    or further process the original payload.
    """

    id: str
    content: list[ContentBlock]
    score: float
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_text(self) -> str:
        """Return a human-readable text representation of the content."""
        return content_blocks_to_str(self.content)


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
        filter: dict[str, Any] | None = None,
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

    async def rename_collection(self, old: str, new: str) -> int:
        """Re-key every document from collection *old* to *new*."""
        ...


__all__ = ["Document", "SearchResult", "VectorStore"]
