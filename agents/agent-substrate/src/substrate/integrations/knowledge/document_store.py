"""In-memory implementation of DocumentStore protocol."""

from __future__ import annotations

from typing import Sequence

from substrate.kernel.document.models import DocumentChunk, DocumentMetadata
from substrate.kernel.document.protocols import DocumentStore


class InMemoryDocumentStore(DocumentStore):
    """In-memory catalog and chunk store conforming to DocumentStore protocol."""

    def __init__(self) -> None:
        self._documents: dict[str, DocumentMetadata] = {}
        self._chunks: dict[str, list[DocumentChunk]] = {}

    async def save_document(
        self, metadata: DocumentMetadata, chunks: Sequence[DocumentChunk]
    ) -> None:
        self._documents[metadata.id] = metadata
        self._chunks[metadata.id] = list(chunks)

    async def get_document(self, document_id: str) -> DocumentMetadata | None:
        return self._documents.get(document_id)

    async def get_chunks(self, document_id: str) -> Sequence[DocumentChunk]:
        return list(self._chunks.get(document_id, []))

    async def list_documents(
        self, *, limit: int = 100, offset: int = 0
    ) -> Sequence[DocumentMetadata]:
        docs = list(self._documents.values())
        return docs[offset : offset + limit]

    async def delete_document(self, document_id: str) -> None:
        self._documents.pop(document_id, None)
        self._chunks.pop(document_id, None)


__all__ = ["InMemoryDocumentStore"]

