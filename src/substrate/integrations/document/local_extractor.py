"""LocalDocumentExtractor — the L1 default ``DocumentExtractor``.

Per page: text-layer extraction first (``pdfplumber``, falling back to
``pypdf`` if ``pdfplumber`` isn't installed), then — only for a page that
still has no usable text, i.e. a scanned/image-only page — a bare-minimum
OCR fallback via Tesseract (``pytesseract`` + ``pypdfium2`` for
rasterization, the ``ocr`` extra). Both OCR imports are lazy: a normal
digital-text PDF never touches them, so Tesseract only needs to be installed
if you actually feed this a scanned document.

This is deliberately the *least* infra a real `DocumentExtractor` can need —
a single well-known OCR binary, no model weights, no GPU, no external
service. For production-grade layout/OCR quality (multi-column reading
order, chart/table detection), see the PaddleOCR-backed adapter in
``runtimes/document_intelligence`` (``ServiceBackedDocumentExtractor``),
which implements this exact same kernel ``DocumentExtractor`` Protocol and
is a drop-in replacement wherever this one is used.
"""

from __future__ import annotations

import io

from substrate.kernel.document import ExtractedPage, ExtractionResult
from substrate.logger import setup_logging

logger = setup_logging()


class LocalDocumentExtractor:
    """``DocumentExtractor`` implementation: pdfplumber/pypdf text layer,
    with a per-page Tesseract OCR fallback for scanned pages."""

    def __init__(self, *, extract_tables: bool = True, ocr: bool = True) -> None:
        self.extract_tables = extract_tables
        self.ocr = ocr

    async def extract(self, data: bytes, filename: str) -> ExtractionResult:
        try:
            pages = await self._extract_with_pdfplumber(data)
        except ImportError:
            logger.info("pdfplumber not available, falling back to pypdf")
            try:
                pages = self._extract_with_pypdf(data)
            except Exception as exc:
                return ExtractionResult(success=False, error=str(exc))
        except Exception as exc:
            return ExtractionResult(success=False, error=str(exc))

        if self.ocr:
            pages = [
                page if page.text.strip() else self._ocr_page(data, page)
                for page in pages
            ]

        markdown = "\n\n".join(p.text for p in pages if p.text)
        return ExtractionResult(
            success=True, pages=pages, markdown=markdown, engine="local"
        )

    async def _extract_with_pdfplumber(self, data: bytes) -> list[ExtractedPage]:
        import pdfplumber  # type: ignore[import-unresolved]

        pages: list[ExtractedPage] = []
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for i, page in enumerate(pdf.pages, start=1):
                parts: list[str] = []
                text = page.extract_text() or ""
                if text.strip():
                    parts.append(text)

                if self.extract_tables:
                    for table in page.extract_tables():
                        rows = [
                            " | ".join(str(c) if c else "" for c in row)
                            for row in table
                        ]
                        if rows:
                            parts.append("\n".join(rows))

                pages.append(
                    ExtractedPage(page_number=i, text="\n\n".join(parts).strip())
                )
        return pages

    def _extract_with_pypdf(self, data: bytes) -> list[ExtractedPage]:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return [
            ExtractedPage(page_number=i, text=(page.extract_text() or "").strip())
            for i, page in enumerate(reader.pages, start=1)
        ]

    def _ocr_page(self, data: bytes, page: ExtractedPage) -> ExtractedPage:
        """Rasterize *page* via pypdfium2 and OCR it with Tesseract.

        Degrades to the original (empty-text) page on any failure — missing
        ``pytesseract``/``pypdfium2``, missing system ``tesseract`` binary,
        or a page that pypdfium2 can't render — never raises.
        """
        try:
            import pypdfium2 as pdfium
            import pytesseract

            pdf = pdfium.PdfDocument(data)
            try:
                bitmap = pdf[page.page_number - 1].render(scale=2.0)
                image = bitmap.to_pil()
            finally:
                pdf.close()
            text = pytesseract.image_to_string(image).strip()
        except Exception as exc:
            logger.info(
                "OCR fallback unavailable/failed for page %d: %s",
                page.page_number,
                exc,
            )
            return page

        return page.model_copy(update={"text": text}) if text else page


__all__ = ["LocalDocumentExtractor"]
