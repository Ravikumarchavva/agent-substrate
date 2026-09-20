"""Document models — shared multimodal value types for extraction, chunking, and RAG.

Includes layout-aware page structures, extracted figure/chart blocks with captions,
multimodal document chunks, and document metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any, Sequence

from pydantic import Field

from substrate.kernel.core.content import ContentBlock, JsonObject, KernelModel, TextBlock


class ExtractedImageLabel(StrEnum):
    """What kind of visual an extracted image crop represents."""

    CHART = "chart"
    TABLE = "table"
    FIGURE = "figure"
    FORMULA = "formula"
    IMAGE = "image"


class ExtractedImage(KernelModel):
    """An image, chart, table, or formula crop extracted from a document page."""

    data: bytes
    media_type: str = "image/png"
    page_number: int | None = None
    label: ExtractedImageLabel = ExtractedImageLabel.CHART
    confidence: float = 0.0
    caption: str | None = None
    id: str = ""

    model_config = {
        "frozen": True,
        "ser_json_bytes": "base64",
        "val_json_bytes": "base64",
    }


class ExtractedPage(KernelModel):
    """A single page of extracted content."""

    page_number: int
    text: str
    markdown: str = ""
    images: Sequence[ExtractedImage] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)


class ExtractionResult(KernelModel):
    """Outcome of a document extraction run."""

    success: bool = True
    pages: Sequence[ExtractedPage] = Field(default_factory=list)
    markdown: str = ""
    engine: str = ""
    error: str | None = None
    degraded_from: str | None = None


class DocumentMetadata(KernelModel):
    """Catalog metadata describing a stored document."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    filename: str = ""
    content_type: str = "application/octet-stream"
    byte_size: int = 0
    total_pages: int = 0
    sha256: str = ""
    created_at: datetime | None = None
    metadata: JsonObject = Field(default_factory=dict)


class DocumentChunk(KernelModel):
    """A discrete passage or multimodal section of a document prepared for retrieval."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    document_id: str = ""
    text: str = ""
    content: Sequence[ContentBlock] = Field(default_factory=list)
    page_number: int | None = None
    chunk_index: int = 0
    token_count: int = 0
    embedding: Sequence[float] | None = None
    metadata: JsonObject = Field(default_factory=dict)

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
        metadata: dict[str, Any] | None = None,
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
    "ExtractedImageLabel",
    "ExtractedImage",
    "ExtractedPage",
    "ExtractionResult",
    "DocumentMetadata",
    "DocumentChunk",
]
