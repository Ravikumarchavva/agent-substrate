"""substrate.documents — Document extraction and chunking contracts, and the local document store."""

from __future__ import annotations

from substrate.documents.local_store import (
    LocalFilesystemDocumentStore,
)
from substrate.documents.protocols import (
    DocumentChunker,
    DocumentExtractor,
    DocumentStore,
)
from substrate.documents.types import (
    DocumentChunk,
    DocumentMetadata,
    ExtractedImage,
    ExtractedImageLabel,
    ExtractedPage,
    ExtractionResult,
)

__all__ = [
    "DocumentChunk",
    "DocumentChunker",
    "DocumentExtractor",
    "DocumentMetadata",
    "DocumentStore",
    "ExtractedImage",
    "ExtractedImageLabel",
    "ExtractedPage",
    "ExtractionResult",
    "LocalFilesystemDocumentStore",
]
