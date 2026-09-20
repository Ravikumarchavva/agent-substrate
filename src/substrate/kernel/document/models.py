"""Document models — shared multimodal value types for extraction, chunking, and RAG.

Includes layout-aware page structures, extracted figure/chart blocks with captions,
multimodal document chunks, and document metadata.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

from substrate.kernel.core.content import ContentBlock, TextBlock


@dataclass(frozen=True)
class ExtractedImage:
    """An image, chart, table, or formula crop extracted from a document page."""

    data: bytes
    media_type: str = "image/png"
    page_number: int | None = None
    label: str = "chart"  # chart | table | figure | formula | image
    confidence: float = 0.0
    caption: str | None = None
    id: str = ""


@dataclass(frozen=True)
class ExtractedPage:
    """A single page of extracted content."""

    page_number: int
    text: str
    markdown: str = ""
    images: Sequence[ExtractedImage] = field(default_factory=list)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExtractionResult:
    """Outcome of a document extraction run."""

    success: bool = True
    pages: Sequence[ExtractedPage] = field(default_factory=list)
    markdown: str = ""
    engine: str = ""
    error: str | None = None
    degraded_from: str | None = None


@dataclass(frozen=True)
class DocumentMetadata:
    """Catalog metadata describing a stored document."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    filename: str = ""
    content_type: str = "application/octet-stream"
    byte_size: int = 0
    total_pages: int = 0
    sha256: str = ""
    created_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DocumentChunk:
    """A discrete passage or multimodal section of a document prepared for retrieval."""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    document_id: str = ""
    text: str = ""
    content: Sequence[ContentBlock] = field(default_factory=list)
    page_number: int | None = None
    chunk_index: int = 0
    token_count: int = 0
    embedding: Sequence[float] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        document_id: str = "",
        page_number: int | None = None,
        chunk_index: int = 0,
        token_count: int = 0,
        id: str | None = None,
        embedding: Sequence[float] | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "DocumentChunk":
        return cls(
            id=id or str(uuid.uuid4()),
            document_id=document_id,
            text=text,
            content=[TextBlock(text=text)],
            page_number=page_number,
            chunk_index=chunk_index,
            token_count=token_count,
            embedding=embedding,
            metadata=metadata or {},
        )


__all__ = [
    "ExtractedImage",
    "ExtractedPage",
    "ExtractionResult",
    "DocumentMetadata",
    "DocumentChunk",
]
