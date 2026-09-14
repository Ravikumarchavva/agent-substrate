"""Declarative, no-recognition-pipeline extraction — mode ``raw_text``.

Implements :class:`DeclarativeExtractionEngine` (see ``base.py``). PDFs go
straight through ``pypdfium2``'s own text layer (no OCR, no layout model —
this mode is for text-native documents); DOCX/PPTX/ODT/RTF delegate to
``convert.py``'s two-tier markitdown/LibreOffice conversion, escalating to a
LibreOffice -> PDF -> pypdfium2 round trip when markitdown's extracted text
comes back thin. Images have no text layer at all and are returned as a
single empty page rather than erroring.

Matches this service's existing "never raise per-document" philosophy (see
``paddle_classic.py``): a scanned/image-only PDF, a corrupt file, or a
markitdown/LibreOffice failure all degrade to an ``ExtractionResult`` with
empty text rather than raising — turning "empty result" into a user-facing
error is ``routes.py``'s job, not this engine's.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from substrate.logger import setup_logging
from substrate.runtimes.document_intelligence.service import convert
from substrate.runtimes.document_intelligence.service.types import (
    ExtractedPage,
    ExtractionResult,
)

logger = setup_logging("substrate.document_intelligence.raw_text")

# Real, standard IANA media types — kept in sync with convert.py's
# CONTENT_TYPE -> extension map (the reverse direction: extension ->
# content-type), since extract() only receives a filename, not a
# content-type, and must dispatch on the extension itself.
_OFFICE_EXT_TO_CONTENT_TYPE: dict[str, str] = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".doc": "application/msword",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".ppt": "application/vnd.ms-powerpoint",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".rtf": "application/rtf",
}

_IMAGE_EXTS = {".png", ".jpg", ".jpeg"}
_IMAGE_CONTENT_TYPES = {"image/png", "image/jpeg"}


def _extract_pdf(data: bytes) -> ExtractionResult:
    """Core PDF text extraction via pypdfium2, shared with ``convert.py``'s
    Tier 2 escalation (LibreOffice's converted-to-PDF bytes get re-run
    through this exact same path). Never raises — a corrupt/unreadable PDF
    returns zero pages instead of propagating pypdfium2's exception."""
    import pypdfium2 as pdfium

    try:
        doc = pdfium.PdfDocument(data)
    except Exception:
        logger.warning("pypdfium2 could not open document — returning empty result")
        return ExtractionResult(pages=[], markdown="", engine=RawTextEngine.name)

    try:
        pages: list[ExtractedPage] = []
        texts: list[str] = []
        for i in range(len(doc)):
            text = ""
            try:
                page = doc[i]
                try:
                    text = page.get_textpage().get_text_range()
                finally:
                    page.close()
            except Exception:
                # A single unreadable page must not fail the whole
                # document — matches paddle_classic.py's per-block
                # never-raise convention, applied here per-page.
                logger.warning("pypdfium2 failed to extract page %d text", i + 1)
                text = ""
            pages.append(ExtractedPage(page_number=i + 1, text=text, markdown=text))
            texts.append(text)
        return ExtractionResult(
            pages=pages,
            markdown="\n\n".join(texts),
            engine=RawTextEngine.name,
        )
    finally:
        doc.close()


class RawTextEngine:
    """No recognition pipeline — converts straight to text/markdown. Mode
    ``raw_text``: pypdfium2 for PDF, markitdown/LibreOffice (via
    ``convert.py``) for Office formats, empty result for images."""

    name = "raw_text"

    def supported_formats(self) -> set[str]:
        return (
            {"application/pdf"} | _IMAGE_CONTENT_TYPES | convert.CONVERTIBLE_CONTENT_TYPES
        )

    def accepts(self, filename: str, content_type: str) -> bool:
        if content_type in self.supported_formats():
            return True
        # octet-stream fallback: some uploads arrive with a generic
        # content-type but a real filename extension — same case
        # convert.py already has to handle for Office uploads.
        ext = Path(filename).suffix.lower()
        return (
            ext == ".pdf"
            or ext in _IMAGE_EXTS
            or ext in _OFFICE_EXT_TO_CONTENT_TYPE
        )

    def warmup(self) -> None:
        """No model to warm up — this engine is pure library calls, no
        one-time load cost to pay ahead of the first request. No-op."""
        pass

    async def aclose(self) -> None:
        """No held resources (no worker pool, no open model). No-op."""
        pass

    def extract(self, data: bytes, filename: str) -> ExtractionResult:
        """Dispatches on *filename*'s extension: pypdfium2 for PDF,
        ``convert.py``'s two-tier conversion for Office formats, an empty
        single-page result for images. Unknown/missing extensions fall
        through to the PDF path, matching ``paddle_classic.py``'s
        "suffix or .pdf" default."""
        ext = Path(filename).suffix.lower()

        if ext in _IMAGE_EXTS:
            page = ExtractedPage(page_number=1, text="", markdown="")
            return ExtractionResult(pages=[page], markdown="", engine=self.name)

        if ext in _OFFICE_EXT_TO_CONTENT_TYPE:
            content_type = _OFFICE_EXT_TO_CONTENT_TYPE[ext]
            # extract() is sync per the DeclarativeExtractionEngine
            # Protocol; convert_office_document is async (Tier 2 shells
            # out via asyncio subprocess). Callers are expected to run
            # extract() off the event loop (e.g. asyncio.to_thread), same
            # as PaginatedExtractionEngine callers do for their sync
            # `extract()` — so asyncio.run() here is safe: no loop is
            # already running in that thread.
            return asyncio.run(
                convert.convert_office_document(data, filename, content_type)
            )

        return _extract_pdf(data)

    def extract_batch(self, items: list[tuple[bytes, str]]) -> list[ExtractionResult]:
        """No cross-document batching to exploit here (unlike
        ``PaddleClassicEngine``, there's no shared model inference call to
        amortize) — simply extracts each item in turn."""
        return [self.extract(data, filename) for data, filename in items]


__all__ = ["RawTextEngine"]
