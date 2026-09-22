"""LocalDocumentExtractor — the L1 default DocumentExtractor: pdfplumber/
pypdf text-layer extraction plus a per-page Tesseract OCR fallback."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from substrate.agents.document import LocalDocumentExtractor

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "test_invoice.pdf"


async def test_extract_returns_text_via_pdfplumber():
    extractor = LocalDocumentExtractor()
    result = await extractor.extract(_FIXTURE.read_bytes(), "invoice.pdf")

    assert result.success
    assert result.engine == "local"
    assert len(result.pages) >= 1
    assert any(p.text.strip() for p in result.pages)


async def test_extract_falls_back_to_pypdf_when_pdfplumber_missing():
    extractor = LocalDocumentExtractor()

    async def _raise_import_error(data: bytes) -> list:
        raise ImportError("no pdfplumber")

    with patch.object(
        extractor, "_extract_with_pdfplumber", side_effect=_raise_import_error
    ):
        result = await extractor.extract(_FIXTURE.read_bytes(), "invoice.pdf")

    assert result.success
    assert len(result.pages) >= 1


async def test_extract_garbage_bytes_returns_failure_not_exception():
    extractor = LocalDocumentExtractor()
    result = await extractor.extract(b"not a pdf at all", "junk.pdf")

    assert result.success is False
    assert result.error


async def test_ocr_fallback_triggers_on_textless_page():
    from substrate.kernel.document import ExtractedPage

    extractor = LocalDocumentExtractor()
    blank_page = ExtractedPage(page_number=1, text="")

    with patch.object(
        extractor,
        "_ocr_page",
        side_effect=lambda data, page: page.model_copy(update={"text": "ocr'd text"}),
    ) as mock_ocr:
        with patch.object(
            extractor, "_extract_with_pdfplumber", return_value=[blank_page]
        ):
            result = await extractor.extract(b"fake-pdf-bytes", "scan.pdf")

    mock_ocr.assert_called_once()
    assert result.pages[0].text == "ocr'd text"


async def test_ocr_degrades_gracefully_when_unavailable():
    """A page with no text layer, and OCR itself failing (e.g. tesseract
    binary not installed), yields empty page text — not an exception."""
    extractor = LocalDocumentExtractor()

    from substrate.kernel.document import ExtractedPage

    blank_page = ExtractedPage(page_number=1, text="")
    with patch.object(
        extractor, "_extract_with_pdfplumber", return_value=[blank_page]
    ):
        with patch("pypdfium2.PdfDocument", side_effect=ImportError("no pypdfium2")):
            result = await extractor.extract(b"fake-pdf-bytes", "scan.pdf")

    assert result.success
    assert result.pages[0].text == ""
