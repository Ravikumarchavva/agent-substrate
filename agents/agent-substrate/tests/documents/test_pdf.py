"""The PDF reader on PDFs built to a known shape (``tests/fixtures/pdfs.py``): the structure it recovers, and what it does with scans."""

from __future__ import annotations

import re

import pytest

from substrate.documents.reading.engine import read_document
from substrate.documents.reading.ocr import TesseractOcr
from substrate.documents.types import ReadLimits
from tests.documents._files import fixture
from tests.fixtures.pdfs import Line, Page, build, scan_of

BODY = "The quarterly review covers shipments, staffing and the regional budget in some detail for the board."


def read(data: bytes, **kw):
    return read_document(data, "doc.pdf", **kw)


def test_the_invoice_is_read_with_its_text_and_one_page() -> None:
    result = read(fixture("test_invoice.pdf"))
    assert result.success and result.engine == "pdfium" and "<!-- page 1 -->" in result.markdown
    assert len(result.pages) >= 1 and "Invoice #12345" in result.pages[0].text


def test_bookmarks_make_the_headings() -> None:
    result = read(fixture("bookmarks.pdf"))
    headings = [line for line in result.markdown.splitlines() if line.startswith("#")]
    assert headings and headings[0].startswith("# ") and any(line.startswith("## ") for line in headings)


def test_without_bookmarks_larger_type_makes_the_headings() -> None:
    pages = [
        Page([Line("Annual Report", size=24, bold=True), Line(BODY, gap=14), Line(BODY), Line("Findings", size=16, bold=True, gap=18), Line(BODY, gap=8), Line(BODY)]),
    ]
    md = read(build(pages)).markdown
    assert "# Annual Report" in md and "## Findings" in md and md.count("#") == 3
    assert BODY in md


def test_a_running_footer_is_dropped_but_page_text_stays() -> None:
    topics = ["shipping", "staffing", "budget", "risks"]
    pages = [Page([Line(f"Section about {t}: " + BODY), Line("ACME Confidential", size=8, gap=680)]) for t in topics]
    result = read(build(pages))
    assert "ACME Confidential" not in result.markdown
    assert all(f"Section about {t}" in result.markdown for t in topics)


def test_a_hyphenated_word_is_joined_across_lines() -> None:
    pages = [Page([Line("The shipment was dis-"), Line("tributed across the regional warehouses on Friday.")])]
    assert "distributed across" in read(build(pages)).pages[0].text


def test_bullets_become_a_tight_list() -> None:
    pages = [Page([Line("Next steps", size=18, bold=True), Line("\xb7 Ship the beta", gap=6), Line("\xb7 Invite customers"), Line("\xb7 Collect feedback")])]
    md = read(build(pages)).markdown
    assert re.search(r"- Ship the beta\n- Invite customers\n- Collect feedback", md)


def test_a_figure_is_extracted_as_a_valid_png_and_linked() -> None:
    rgb = bytes([200, 30, 30]) * (240 * 200)
    result = read(build([Page([Line("Revenue by region")], figure=(240, 200, rgb))]))
    page = result.pages[0]
    assert len(page.images) == 1 and page.images[0].data[:8] == b"\x89PNG\r\n\x1a\n"
    assert f"(cid:{page.images[0].id})" in result.markdown


def test_a_scanned_page_without_ocr_is_reported_never_silently_empty() -> None:
    result = read(fixture("scanned_page.pdf"), ocr=None)
    assert result.success and result.needs_ocr == [1]
    assert result.pages[0].needs_ocr and any("OCR" in w for w in result.warnings)


@pytest.mark.skipif(not TesseractOcr.available(), reason="tesseract is not installed")
def test_a_scanned_page_is_recognised_when_there_is_ocr() -> None:
    result = read(fixture("scanned_page.pdf"), ocr=TesseractOcr())
    assert "4417" in result.pages[0].text and result.pages[0].method == "ocr" and not result.needs_ocr


@pytest.mark.skipif(not TesseractOcr.available(), reason="tesseract is not installed")
def test_fast_never_ocrs_and_ocr_only_reads_every_page() -> None:
    assert read(fixture("scanned_page.pdf"), ocr=TesseractOcr(), strategy="fast").needs_ocr == [1]
    width, height, pixels = scan_of([Line("Invoice 4417 total due 120 EUR", size=24)])
    mixed = build([Page([Line("Typed page " + BODY)]), Page(image=(width, height, pixels))])
    result = read(mixed, ocr=TesseractOcr(), strategy="ocr_only")
    assert [p.method for p in result.pages] == ["ocr", "ocr"] and "4417" in result.pages[1].text


def test_a_password_protected_pdf_fails_with_a_reason() -> None:
    pypdf = pytest.importorskip("pypdf")
    import io

    writer = pypdf.PdfWriter()
    writer.append(pypdf.PdfReader(io.BytesIO(fixture("test_invoice.pdf"))))
    writer.encrypt("secret")
    buffer = io.BytesIO()
    writer.write(buffer)
    result = read(buffer.getvalue())
    assert not result.success and "password" in result.error.lower()


def test_a_garbage_pdf_and_a_page_limit() -> None:
    assert not read(b"%PDF-1.4\n" + bytes(range(256)) * 10).success
    pages = [Page([Line(f"Page {n} " + BODY)]) for n in range(1, 6)]
    capped = read(build(pages), limits=ReadLimits(max_pages=2))
    assert len(capped.pages) == 2 and any("first 2 of 5" in w for w in capped.warnings)
