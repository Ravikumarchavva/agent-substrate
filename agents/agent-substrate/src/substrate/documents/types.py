"""Document models — shared multimodal value types for extraction, chunking, and RAG.

Includes layout-aware page structures, extracted figure/chart blocks with captions,
multimodal document chunks, and document metadata.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Sequence

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


# ── what a document library answers with ─────────────────────────────────────────────────────────────────


class DocumentError(ValueError):
    """A document could not be added: it could not be read, or it has nothing readable in it."""


@dataclass(frozen=True)
class Added:
    document: str
    title: str
    filename: str
    pages: int
    sections: int
    images: int
    needs_ocr: tuple[int, ...] = ()
    duplicate: bool = False
    """The same file was already in the collection: nothing was written."""
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentInfo:
    document: str
    title: str
    filename: str
    pages: int
    sections: int
    images: int
    needs_ocr: tuple[int, ...]
    engine: str
    resource: str | None
    meta: dict[str, Any] = field(default_factory=dict)
    description: str | None = None
    """A model-written card for the document (``Library.enrich``), or ``None`` while it only has the counted index."""
    topics: tuple[str, ...] = ()
    """Where the document is filed (slash-separated paths), for a library that files its documents."""
    enrichment: str = "none"
    """``none``, ``running``, ``done`` or ``failed``."""


@dataclass(frozen=True)
class SectionInfo:
    position: int
    title: str
    heading_path: tuple[str, ...]
    first_page: int
    last_page: int
    tokens: int
    headings: tuple[str, ...] = ()
    """The headings inside the section, for a section that holds several."""
    description: str | None = None
    """What the section says, as a model wrote it (``Library.enrich``)."""


@dataclass(frozen=True)
class Outline:
    document: DocumentInfo
    sections: tuple[SectionInfo, ...]
    more: int = 0
    """Entries beyond ``MAX_OUTLINE`` that are not listed."""
    headings: tuple[tuple[int, str, int], ...] = ()
    """For one section: ``(level, heading, page)`` inside it."""


@dataclass(frozen=True)
class Passage:
    document: DocumentInfo
    section: SectionInfo
    text: str
    offset: int = 0
    next_offset: int | None = None
    """Where to continue (``offset=``) when the text was cut at ``MAX_READ_CHARS``; ``None`` at the end."""
    total_chars: int = 0


@dataclass(frozen=True)
class Hit:
    document: DocumentInfo
    section: SectionInfo
    snippet: str
    score: float
    image: str | None = None
    """For a figure or chart that matched: its id, to ``view``."""


class Hits(list[Hit]):
    """The hits of a ``find``, best first, and ``note`` — what the model should be told about how they were found (the embedding service
    was down, so only words were matched)."""

    note: str | None = None


@dataclass(frozen=True)
class Listing:
    documents: tuple[DocumentInfo, ...]
    next: str | None = None
    """Pass as ``after=`` for the next page; ``None`` when this was the last."""


@dataclass(frozen=True)
class Topic:
    """One level of the filing tree: ``browse``'s answer."""

    path: str
    children: tuple[tuple[str, int], ...]
    """``(topic path, documents below it)`` for each topic directly under ``path``."""
    documents: tuple[DocumentInfo, ...]
    """The documents filed at ``path`` itself."""
    more: int = 0


@dataclass(frozen=True)
class Picture:
    data: bytes
    media_type: str
    document: str
    image: str
    page: int
    label: str
    caption: str | None


@dataclass(frozen=True)
class EnrichmentState:
    """Where a document's description stands (``DocumentLibrary.enrichment_state``)."""

    state: str
    """``running``, ``done`` or ``failed``."""
    enricher: str
    version: str
    attempts: int
    updated_at: float


__all__ = [
    "Added",
    "DocumentError",
    "DocumentInfo",
    "EnrichmentState",
    "ExtractedImageLabel",
    "ExtractedImage",
    "ExtractedPage",
    "ExtractionResult",
    "Hit",
    "Hits",
    "Listing",
    "OcrResult",
    "Outline",
    "Passage",
    "Picture",
    "ReadLimits",
    "SectionInfo",
    "Strategy",
    "Topic",
]
