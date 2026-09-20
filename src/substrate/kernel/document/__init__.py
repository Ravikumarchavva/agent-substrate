"""substrate.kernel.document — document intelligence, extraction, chunking, and storage contracts."""

from __future__ import annotations

from substrate.kernel.document.models import (
    DocumentChunk,
    DocumentMetadata,
    ExtractedImage,
    ExtractedPage,
    ExtractionResult,
)
from substrate.kernel.document.protocols import (
    DocumentChunker,
    DocumentExtractor,
    DocumentStore,
)

__all__ = [
    "ExtractedImage",
    "ExtractedPage",
    "ExtractionResult",
    "DocumentMetadata",
    "DocumentChunk",
    "DocumentExtractor",
    "DocumentChunker",
    "DocumentStore",
]
