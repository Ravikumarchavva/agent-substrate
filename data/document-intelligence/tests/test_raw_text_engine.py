"""``RawTextEngine`` (mode ``raw_text``) — pypdfium2 PDF text extraction, no
OCR/layout model. Real pypdfium2 calls throughout (it's already a
transitive dependency of paddlex, no importorskip needed)."""

from __future__ import annotations

import io

import pypdfium2 as pdfium

from substrate.runtimes.document_intelligence.service.engines.raw_text import (
    RawTextEngine,
)


def _make_pdf_bytes(n_pages: int) -> bytes:
    doc = pdfium.PdfDocument.new()
    try:
        for _ in range(n_pages):
            doc.new_page(200, 200)
        buf = io.BytesIO()
        doc.save(buf)
        return buf.getvalue()
    finally:
        doc.close()


def test_supported_formats_covers_pdf_images_and_office() -> None:
    engine = RawTextEngine()
    formats = engine.supported_formats()
    assert "application/pdf" in formats
    assert "image/png" in formats
    assert "image/jpeg" in formats
    assert (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        in formats
    )


def test_accepts_by_content_type() -> None:
    engine = RawTextEngine()
    assert engine.accepts("doc.pdf", "application/pdf")
    assert not engine.accepts("doc.xlsx", "application/vnd.ms-excel")


def test_accepts_by_extension_when_content_type_is_octet_stream() -> None:
    """Browsers routinely send application/octet-stream for Office
    uploads — a real, common case the engine must still accept."""
    engine = RawTextEngine()
    assert engine.accepts("report.docx", "application/octet-stream")
    assert engine.accepts("slides.pptx", "application/octet-stream")
    assert not engine.accepts("data.bin", "application/octet-stream")


def test_extract_multi_page_pdf_has_continuous_page_numbers() -> None:
    engine = RawTextEngine()
    data = _make_pdf_bytes(4)

    result = engine.extract(data, "doc.pdf")

    assert result.engine == "raw_text"
    assert [p.page_number for p in result.pages] == [1, 2, 3, 4]
    # Blank synthetic pages have no text layer — must not raise, just empty.
    assert all(p.text == "" for p in result.pages)


def test_extract_corrupt_pdf_returns_empty_result_not_raise() -> None:
    engine = RawTextEngine()
    result = engine.extract(b"not a real pdf", "doc.pdf")
    assert result.pages == []
    assert result.engine == "raw_text"


def test_extract_image_returns_single_empty_page() -> None:
    engine = RawTextEngine()
    result = engine.extract(b"\x89PNG\r\n fake", "photo.png")
    assert len(result.pages) == 1
    assert result.pages[0].text == ""
    assert result.engine == "raw_text"


def test_extract_batch_processes_each_item_independently() -> None:
    engine = RawTextEngine()
    items = [(_make_pdf_bytes(1), "a.pdf"), (_make_pdf_bytes(2), "b.pdf")]

    results = engine.extract_batch(items)

    assert len(results) == 2
    assert len(results[0].pages) == 1
    assert len(results[1].pages) == 2


def test_warmup_and_aclose_are_safe_noops() -> None:
    engine = RawTextEngine()
    engine.warmup()  # must not raise, no model to warm


async def test_aclose_is_a_safe_noop() -> None:
    engine = RawTextEngine()
    await engine.aclose()  # must not raise, no held resources
