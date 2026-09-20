"""Document protocols — contracts for extraction, chunking, and document storage."""

from __future__ import annotations

from typing import Protocol, Sequence

from substrate.kernel.document.models import (
    DocumentChunk,
    DocumentMetadata,
    ExtractionResult,
)


class DocumentExtractor(Protocol):
    """Contract every document extraction backend satisfies."""

    async def extract(self, data: bytes, filename: str) -> ExtractionResult: ...


class DocumentChunker(Protocol):
    """Contract for splitting extracted document pages into retrieval-ready chunks."""

    def chunk(
        self,
        result: ExtractionResult,
        *,
        chunk_size: int = 500,
        chunk_overlap: int = 50,
    ) -> Sequence[DocumentChunk]: ...


class DocumentStore(Protocol):
    """Durable catalog and chunk store for ingested documents."""

    async def save_document(
        self, metadata: DocumentMetadata, chunks: Sequence[DocumentChunk]
    ) -> None: ...

    async def get_document(self, document_id: str) -> DocumentMetadata | None: ...

    async def get_chunks(self, document_id: str) -> Sequence[DocumentChunk]: ...

    async def list_documents(
        self, *, limit: int = 100, offset: int = 0
    ) -> Sequence[DocumentMetadata]: ...

    async def delete_document(self, document_id: str) -> None: ...


__all__ = [
    "DocumentExtractor",
    "DocumentChunker",
    "DocumentStore",
]
