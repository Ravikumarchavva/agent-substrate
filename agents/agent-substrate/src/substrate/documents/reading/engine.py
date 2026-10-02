"""The built-in engine: bytes in, ``ExtractionResult`` out, synchronously, in whatever process calls it.

``Reader`` runs this in an isolated worker process (or a thread); this module knows nothing about processes. It never raises for a
document it cannot read — corrupt, encrypted, hostile or unsupported files come back as ``ExtractionResult(success=False, error=…)``.
"""

from __future__ import annotations

import logging
import time

from substrate.documents.protocols import Ocr
from substrate.documents.reading.build import PageBuilder, document_markdown
from substrate.documents.reading.docx import read_docx
from substrate.documents.reading.html import html_to_markdown
from substrate.documents.reading.odf import read_odf
from substrate.documents.reading.pdf import ReadTimeout, read_pdf
from substrate.documents.reading.pptx import read_pptx
from substrate.documents.reading.sniff import Format, sniff
from substrate.documents.reading.text import csv_to_markdown, decode_text, json_to_markdown, plain_text
from substrate.documents.reading.xlsx import read_xlsx
from substrate.documents.reading.xmlsafe import Package, UnsafeDocument
from substrate.documents.types import ExtractedPage, ExtractionResult, ReadLimits

logger = logging.getLogger(__name__)

CONTENT_TYPES = {
    Format.PDF: "application/pdf",
    Format.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    Format.PPTX: "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    Format.XLSX: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    Format.ODT: "application/vnd.oasis.opendocument.text",
    Format.ODP: "application/vnd.oasis.opendocument.presentation",
    Format.ODS: "application/vnd.oasis.opendocument.spreadsheet",
    Format.HTML: "text/html",
    Format.MARKDOWN: "text/markdown",
    Format.TEXT: "text/plain",
    Format.CSV: "text/csv",
    Format.TSV: "text/tab-separated-values",
    Format.JSON: "application/json",
    Format.IMAGE: "image/*",
}

_UNSUPPORTED = {
    Format.OLE: "this is a legacy binary Office file (.doc/.ppt/.xls). Save it as .docx/.pptx/.xlsx, or read it with a document server "
    "(Reader('http://…')), which converts it with LibreOffice",
    Format.OTHER_ARCHIVE: "this is an archive of a type this reader does not open (not an Office or OpenDocument file)",
    Format.UNKNOWN: "this does not look like a document: it is not text and not a format this reader knows",
}


def _failure(error: str, fmt: Format | None = None, engine: str = "native") -> ExtractionResult:
    return ExtractionResult(success=False, error=error, engine=engine, content_type=CONTENT_TYPES.get(fmt, "") if fmt else "")


def _single_page(markdown: str, *, method: str = "native") -> list[ExtractedPage]:
    return [ExtractedPage(page_number=1, text=plain_text(markdown), markdown=markdown, method=method)]  # type: ignore[arg-type]


def read_document(
    data: bytes,
    filename: str = "",
    content_type: str | None = None,
    *,
    strategy: str = "auto",
    limits: ReadLimits | None = None,
    ocr: Ocr | None = None,
    languages: tuple[str, ...] = ("eng",),
    deadline: float | None = None,
) -> ExtractionResult:
    limits = limits or ReadLimits()
    if len(data) > limits.max_bytes:
        return _failure(f"the file is {len(data):,} bytes; the limit is {limits.max_bytes:,}")
    if not data:
        return _failure("the file is empty")
    fmt = sniff(data, filename, content_type)
    if fmt == Format.UNKNOWN and data[:2] == b"PK":
        return _failure("this is not a valid zip container (a damaged .docx/.pptx/.xlsx/.odt?)")
    try:
        return _dispatch(data, fmt, strategy, limits, ocr, languages, deadline)
    except ReadTimeout:
        return _failure("reading took too long and was stopped", fmt)
    except UnsafeDocument as exc:
        return _failure(str(exc), fmt)
    except RecursionError:
        return _failure("the document is nested too deeply", fmt)
    except Exception as exc:  # noqa: BLE001 — a document that breaks a reader is a failure of that document
        logger.warning("could not read %s (%s): %s", filename or "<unnamed>", fmt.value, exc, exc_info=True)
        return _failure(f"could not read this {fmt.value} document: {exc}", fmt)


def _dispatch(data: bytes, fmt: Format, strategy: str, limits: ReadLimits, ocr: Ocr | None, languages: tuple[str, ...], deadline: float | None) -> ExtractionResult:
    if fmt == Format.PDF:
        result = read_pdf(data, limits=limits, ocr=ocr, strategy=strategy, languages=languages, deadline=deadline)
        return result
    if fmt in (Format.DOCX, Format.PPTX, Format.XLSX, Format.ODT, Format.ODP, Format.ODS):
        pkg = Package(data)
        try:
            if fmt == Format.DOCX:
                pages, title, warnings = read_docx(pkg)
            elif fmt == Format.PPTX:
                pages, title, warnings = read_pptx(pkg)
            elif fmt == Format.XLSX:
                pages, title, warnings = read_xlsx(pkg)
            else:
                pages, title, warnings = read_odf(pkg, fmt.value)
        finally:
            pkg.close()
        if len(pages) > limits.max_pages:
            warnings = [*warnings, f"only the first {limits.max_pages} of {len(pages)} pages were read"]
            pages = pages[: limits.max_pages]
        return ExtractionResult(
            pages=pages, markdown=document_markdown(pages), engine="native", content_type=CONTENT_TYPES[fmt], title=title, warnings=warnings
        )
    if fmt == Format.HTML:
        markdown, title = html_to_markdown(decode_text(data))
        pages = _single_page(markdown)
        return ExtractionResult(pages=pages, markdown=document_markdown(pages), engine="native", content_type=CONTENT_TYPES[fmt], title=title)
    if fmt in (Format.MARKDOWN, Format.TEXT, Format.CSV, Format.TSV, Format.JSON):
        text = decode_text(data)
        warnings: list[str] = []
        if fmt in (Format.CSV, Format.TSV):
            markdown, warnings = csv_to_markdown(text, delimiter="\t" if fmt == Format.TSV else None)
        elif fmt == Format.JSON:
            markdown = json_to_markdown(text)
        else:
            markdown = text.strip()
        title = next((line.lstrip("# ").strip() for line in markdown.splitlines() if line.startswith("# ")), None) if fmt == Format.MARKDOWN else None
        pages = _single_page(markdown)
        return ExtractionResult(pages=pages, markdown=document_markdown(pages), engine="native", content_type=CONTENT_TYPES[fmt], title=title, warnings=warnings)
    if fmt == Format.IMAGE:
        return _image(data, strategy, ocr, languages)
    return _failure(_UNSUPPORTED[fmt], fmt)


def _image(data: bytes, strategy: str, ocr: Ocr | None, languages: tuple[str, ...]) -> ExtractionResult:
    """A picture of text: its words through OCR, or one page that says it needs OCR."""
    if ocr is None or strategy == "fast":
        page = ExtractedPage(page_number=1, text="", markdown="", method="text", needs_ocr=True)
        warning = "this is an image and no OCR engine is installed (apt install tesseract-ocr, or pip install 'agent-substrate[ocr]')"
        if strategy == "fast":
            warning = "strategy='fast' does not run OCR: the image is listed in needs_ocr"
        return ExtractionResult(pages=[page], markdown=document_markdown([page]), engine="native", content_type="image/*", warnings=[warning])
    result = ocr.recognize(data, languages=languages)
    if result.error:
        page = ExtractedPage(page_number=1, text="", markdown="", method="text", needs_ocr=True)
        return ExtractionResult(pages=[page], markdown=document_markdown([page]), engine=f"native+{ocr.name}", content_type="image/*", warnings=[f"OCR failed: {result.error}"])
    builder = PageBuilder(method="ocr")
    for paragraph in result.text.split("\n\n"):
        builder.block(" ".join(paragraph.split("\n")).strip())
    pages = builder.pages(keep_empty=True)
    warnings = [f"low OCR confidence ({result.confidence:.0f}): the text may contain errors"] if result.text.strip() and result.confidence < 60 else []
    return ExtractionResult(pages=pages, markdown=document_markdown(pages), engine=f"native+{ocr.name}", content_type="image/*", warnings=warnings)


def budget(page_hint: int, limits: ReadLimits) -> float:
    """The wall-clock seconds a read may take: a base, plus a share per page, capped."""
    return min(limits.max_timeout_s, limits.timeout_s + limits.per_page_s * max(0, page_hint))


def deadline_for(limits: ReadLimits, page_hint: int = 0) -> float:
    return time.monotonic() + budget(page_hint, limits)


__all__ = ["CONTENT_TYPES", "budget", "deadline_for", "read_document"]
