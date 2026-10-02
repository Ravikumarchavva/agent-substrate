"""substrate.documents — Document extraction and chunking contracts, and the document catalog."""

from __future__ import annotations

from substrate.documents.tables import (
    Documents,
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
    "Documents",
]
