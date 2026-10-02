"""Which reader answers a request — the one place that decides.

* **Legacy binary Office / RTF** → LibreOffice converts it to a PDF, which then goes the PDF way (no LibreOffice → a clear failure).
* **PDF or image, ``strategy`` ``hi_res`` or ``ocr_only``** → the layout engine (PaddleOCR-VL / PP-StructureV3), if this pod has one.
* **PDF or image, ``auto``** → the built-in reader first; the layout engine only when it found pages it cannot read (``needs_ocr``).
* **``fast``, and everything that is not a PDF or an image** (DOCX, PPTX, XLSX, ODF, HTML, Markdown, text, CSV, JSON) → the built-in reader.

A layout engine that fails is not the end: the built-in reader's answer is returned, with ``degraded_from`` naming the engine.
"""

from __future__ import annotations

import logging
from pathlib import PurePath

from substrate.documents import ExtractionResult, Strategy
from substrate.documents.reading.sniff import Format, sniff

from . import convert
from .engines.base import ExtractionEngine
from .engines.native import NativeEngine

logger = logging.getLogger(__name__)


def _with_page_markers(result: ExtractionResult) -> ExtractionResult:
    """A layout engine's markdown, in the contract's shape: a ``<!-- page N -->`` marker before every page."""
    if not result.success or not result.pages or "<!-- page 1 -->" in result.markdown:
        return result
    parts = [f"<!-- page {p.page_number} -->\n\n{(p.markdown or p.text).strip()}".rstrip() for p in result.pages]
    return result.model_copy(update={"markdown": "\n\n".join(parts)})


async def read(
    native: NativeEngine,
    engine: ExtractionEngine,
    data: bytes,
    filename: str,
    content_type: str,
    strategy: Strategy,
) -> ExtractionResult:
    if convert.is_legacy(data, filename, content_type):
        pdf = await convert.convert_via_libreoffice(data, filename)
        if pdf is None:
            return ExtractionResult(
                success=False,
                engine=native.name,
                error="this is a legacy binary Office or RTF file; reading it needs LibreOffice, which is not available on this server. "
                "Save it as DOCX, PPTX, XLSX or PDF and upload that.",
            )
        data, filename, content_type = pdf, PurePath(filename).stem + ".pdf", "application/pdf"

    layout = engine is not native and sniff(data, filename, content_type) in (Format.PDF, Format.IMAGE)
    if not layout or strategy == "fast":
        return await native.aextract(data, filename, content_type=content_type, strategy=strategy)

    if strategy == "auto":
        first = await native.aextract(data, filename, content_type=content_type, strategy="auto")
        if first.success and not first.needs_ocr:
            return first
    try:
        result = await engine.aextract(data, filename)
    except Exception as exc:  # noqa: BLE001 — an engine failure must degrade, not 500
        logger.warning("%s failed for %r: %s", engine.name, filename, exc)
        result = ExtractionResult(success=False, engine=engine.name, error=str(exc)[:500])
    if result.success and (result.markdown.strip() or any(p.images for p in result.pages)):
        return _with_page_markers(result)
    fallback = await native.aextract(data, filename, content_type=content_type, strategy="auto" if strategy == "hi_res" else strategy)
    note = f"{engine.name}: {result.error or 'found no content'}; read with the built-in reader"
    return fallback.model_copy(update={"degraded_from": engine.name, "warnings": [*fallback.warnings, note]})
