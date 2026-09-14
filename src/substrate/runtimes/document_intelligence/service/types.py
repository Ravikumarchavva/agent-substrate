"""Shared extraction DTOs — moved out of ``pipeline.py`` so every engine
(``raw_text``, ``vl_cpu``/``vl_gpu``, ``ocr_classic``) speaks the same result
shape without importing the classic-engine module. ``pipeline.py`` re-exports
these for back-compat (anything importing ``ExtractedImage`` etc. from there
keeps working unchanged).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ExtractedImage:
    data: bytes
    media_type: str = "image/png"
    page_number: int | None = None
    label: str = "chart"
    confidence: float = 0.0
    # OCR'd text for this block, already computed by the same layout pass
    # (``block.content``) — kept alongside the crop so a confident
    # chart/table is still findable by lexical/exact-text search, not only
    # by visual similarity. ``None`` when OCR produced no text for the block.
    caption: str | None = None
    # Stable id (e.g. "img-p3-0") — the markdown field below references
    # this image via a "cid:{id}" link instead of PaddleX's own
    # filesystem-relative path, which isn't resolvable outside the process.
    id: str = ""


@dataclass(slots=True)
class ExtractedPage:
    page_number: int
    text: str
    images: list[ExtractedImage] = field(default_factory=list)
    # This page's markdown (real reading order, images inline via "cid:{id}"
    # links, tables as HTML/markdown). Populated by every engine, including
    # raw_text (there it's just the plain text, no layout to speak of).
    markdown: str = ""


@dataclass(slots=True)
class ExtractionResult:
    pages: list[ExtractedPage]
    # Whole-document markdown, pages joined with paragraph continuation
    # across page breaks where the engine can do that (PaddleX's own
    # concatenate_markdown_pages for the paginated engines; a plain join
    # for raw_text, which has no cross-page structure to preserve).
    markdown: str = ""
    # Which concrete engine produced this result — "raw_text" |
    # "paddleocr-vl" | "ppstructurev3" | future engines. Feeds
    # ExtractResponse.engine (routes.py) so callers/logs know what actually
    # ran, not a hardcoded placeholder.
    engine: str = ""
    # Set when the deployment/request asked for one mode but got served by
    # another because the requested one couldn't be satisfied on this
    # hardware (e.g. vl_gpu requested, no GPU present -> vl_cpu served).
    # Surfaced in /v1/health and can be echoed per-response so a silent
    # capability downgrade is never actually silent.
    degraded_from: str | None = None


__all__ = ["ExtractedImage", "ExtractedPage", "ExtractionResult"]
