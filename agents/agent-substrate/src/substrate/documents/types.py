"""Document models — shared multimodal value types for extraction, chunking, and RAG.

Includes layout-aware page structures, extracted figure/chart blocks with captions,
multimodal document chunks, and document metadata.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Sequence

from pydantic import Field, model_validator

from substrate.types.content import JsonObject, KernelModel


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


PageMethod = Literal["text", "ocr", "layout", "native"]
"""How a page's text was obtained: read from the text layer, recognised from pixels, laid out by a layout model, or taken from a
format that has text natively (Office, HTML, plain text)."""


class ExtractedPage(KernelModel):
    """A single page of extracted content. Slides and sheets are pages too."""

    page_number: int
    text: str
    markdown: str = ""
    images: Sequence[ExtractedImage] = Field(default_factory=list)
    metadata: JsonObject = Field(default_factory=dict)
    method: PageMethod = "text"
    needs_ocr: bool = False
    """The page has pixels but no readable text and nothing here could recognise it: say so rather than return it empty."""


class ExtractionResult(KernelModel):
    """Outcome of reading a document. ``markdown`` is the whole document with a ``<!-- page N -->`` marker before each page;
    this JSON is also what a document server answers with."""

    success: bool = True
    pages: Sequence[ExtractedPage] = Field(default_factory=list)
    markdown: str = ""
    engine: str = ""
    error: str | None = None
    degraded_from: str | None = None
    content_type: str = ""
    title: str | None = None
    warnings: Sequence[str] = Field(default_factory=list)

    @property
    def needs_ocr(self) -> list[int]:
        """Numbers of the pages that have no readable text and were not recognised."""
        return [page.page_number for page in self.pages if page.needs_ocr]

    @model_validator(mode="after")
    def _success_excludes_error(self) -> "ExtractionResult":
        if self.success and self.error:
            raise ValueError("a successful extraction cannot carry an error")
        if not self.success and not self.error:
            raise ValueError("a failed extraction must say why")
        return self


class OcrResult(KernelModel):
    """What an OCR engine read from one page image."""

    text: str = ""
    confidence: float = 0.0
    """Mean confidence, 0–100."""
    error: str | None = None
    """Why nothing was read when the engine itself failed (timed out, crashed) — distinct from a page with no text."""


Strategy = Literal["fast", "auto", "hi_res", "ocr_only"]
"""``fast`` never runs OCR. ``auto`` runs it only on pages with no text layer. ``ocr_only`` runs it on every page.
``hi_res`` also runs a layout model on every page — only a document server has one; in-process it degrades to ``auto``."""


class ReadLimits(KernelModel):
    """What a read may cost. Exceeding one is a failure with a reason (or, for pages, a truncation with a warning), never a hang."""

    max_bytes: int = 100 * 1024 * 1024
    max_pages: int = 2000
    max_ocr_pages: int = 200
    timeout_s: float = 30.0
    """Wall-clock budget before the page-dependent allowance."""
    per_page_s: float = 2.0
    max_timeout_s: float = 300.0
    memory_bytes: int = 1536 * 1024 * 1024
    """Address-space limit of an isolated reader (enforced on Linux)."""


__all__ = [
    "ExtractedImageLabel",
    "ExtractedImage",
    "ExtractedPage",
    "ExtractionResult",
]
