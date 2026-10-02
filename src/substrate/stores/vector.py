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

from typing import Any, Protocol, Sequence, runtime_checkable

from pydantic import Field

from substrate.types.content import (
    ContentBlock,
    JsonObject,
    KernelModel,
    TextBlock,
    content_blocks_to_str,
)
from substrate.types.ids import new_id


class Document(KernelModel):
    """A content chunk with optional metadata ready for vector storage.

    ``content`` is a sequence of ``ContentBlock`` — text, images, audio,
    structured data, or any mix.  Use ``Document.from_text(s)`` for the
    common case of plain-text chunks.

    ``embedding`` is optional: if provided, the store skips embedding
    (useful when the caller pre-computes embeddings or when the store
    supports server-side embedding and the field is ignored).
    """

    content: Sequence[ContentBlock] = Field(default_factory=list)
    id: str = Field(default_factory=lambda: new_id())
    embedding: Sequence[float] | None = None
    metadata: JsonObject = Field(default_factory=dict)

    # ── Convenience constructors ───────────────────────────────────────────

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        id: str | None = None,
        embedding: Sequence[float] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "Document":
        """Create a text-only document — the common case for plain-text RAG."""
        return cls(
            content=[TextBlock(text=text)],
            id=id or new_id(),
            embedding=embedding,
            metadata=metadata or {},
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    def to_text(self) -> str:
        """Return a human-readable text representation of the content.

        Suitable for embedding, display, or passing to an LLM as context.
        Each block contributes its own text via ``str(block)``.
        """
        return content_blocks_to_str(self.content)


class SearchResult(KernelModel):
    """A single result from a vector similarity search.

    ``content`` mirrors ``Document.content`` — the same multimodal blocks
    that were stored are returned unchanged so callers can render, embed,
    or further process the original payload.
    """

    id: str
    content: Sequence[ContentBlock]
    score: float
    metadata: JsonObject = Field(default_factory=dict)

    def to_text(self) -> str:
        """Return a human-readable text representation of the content."""
        return content_blocks_to_str(self.content)


@runtime_checkable
class VectorStore(Protocol):
    """Contract every vector store adapter must satisfy."""

    async def add(
        self,
        documents: list[Document],
        *,
        collection: str = "default",
        space: str | None = None,
    ) -> list[str]:
        """Persist *documents* and return their ids. The store does not embed: a document with no ``embedding`` is stored without one
        (found by its words, not by similarity). ``space`` names the embedder the vectors came from; a collection holds one embedder's
        vectors, and another's raises ``VectorSpaceError``."""
        ...

    async def search(
        self,
        query_embedding: list[float],
        *,
        collection: str = "default",
        limit: int = 5,
        filter: dict[str, Any] | None = None,
        space: str | None = None,
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
        space: str | None = None,
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


@runtime_checkable
class SearchableVectorStore(VectorStore, Protocol):
    """A vector store that also searches by words, and by both: what retrieval over a knowledge base needs."""

    async def lexical_search(
        self,
        query_text: str,
        *,
        collection: str = "default",
        limit: int = 5,
        filter: dict[str, Any] | None = None,
        match: str = "all",
    ) -> list[SearchResult]:
        """Full-text matches, best first: every word (``match="all"``) or any of them (``"any"``)."""
        ...

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
        filter: dict[str, Any] | None = None,
        space: str | None = None,
    ) -> list[SearchResult]:
        """Dense and word search fused by rank (Reciprocal Rank Fusion)."""
        ...


__all__ = ["Document", "SearchResult", "SearchableVectorStore", "VectorStore"]
