"""Document-extraction contracts — Protocol and shared value types.

Mirrors vector.py/graph.py/history.py/memory.py's shape: a Protocol any
extractor implementation satisfies, plus the dataclasses it returns.
Deliberately minimal — no engine-specific fields (markdown, HTML tables,
confidence scores) belong here; those stay on each implementation's own
richer return type. Two real implementations: runtimes/document_intelligence's
PPStructureV3-backed pipeline (heavy, OCR+layout model, HTTP-only) and
capabilities/knowledge/loaders/xycut_extractor.py's pdfplumber+recursive-xy-cut
extractor (lightweight, digital-PDF-only, in-process, no ML model).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ExtractedImage:
    data: bytes
    media_type: str = "image/png"
    page_number: int | None = None
    label: str = "chart"


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    images: list[ExtractedImage] = field(default_factory=list)


@dataclass(frozen=True)
class ExtractionResult:
    success: bool
    pages: list[ExtractedPage] = field(default_factory=list)
    error: str | None = None


class DocumentExtractor(Protocol):
    """Contract every document-extraction backend satisfies: raw bytes in,
    layout-aware pages (+ any extractable images) out."""

    async def extract(self, data: bytes, filename: str) -> ExtractionResult: ...


__all__ = ["DocumentExtractor", "ExtractedImage", "ExtractedPage", "ExtractionResult"]
