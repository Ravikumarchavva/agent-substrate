"""Conformance suite for ``Ocr``: what every text recogniser must do.

A recogniser reads a PNG of printed text and returns it as ``OcrResult(text, confidence 0–100)``. It is only handed a picture, so it must tolerate
a blank page (empty text, no error), noise and bytes that are not an image at all — those come back as ``OcrResult(error=…)`` or empty
text, **never as an exception**: one bad page must not end a document's read.
"""

from __future__ import annotations


from substrate.documents.protocols import Ocr
from substrate.documents.reading.png import encode_png


class OcrConformance:
    """Subclass and provide an ``ocr`` fixture (an ``Ocr``) and ``invoice_png`` (a PNG of the text ``Invoice 4417 total due 120 EUR``)."""

    def test_it_is_named(self, ocr: Ocr) -> None:
        assert isinstance(ocr.name, str) and ocr.name

    def test_printed_text_is_read(self, ocr: Ocr, invoice_png: bytes) -> None:
        result = ocr.recognize(invoice_png, languages=("eng",))
        assert not result.error, result.error
        assert "4417" in result.text and "Invoice" in result.text
        assert 0.0 <= result.confidence <= 100.0

    def test_a_blank_page_reads_as_nothing_without_failing(self, ocr: Ocr) -> None:
        blank = encode_png(200, 100, bytes([255]) * (200 * 100))
        result = ocr.recognize(blank, languages=("eng",))
        assert result.text.strip() == ""

    def test_bytes_that_are_not_an_image_are_an_error_not_an_exception(
        self, ocr: Ocr
    ) -> None:
        result = ocr.recognize(b"definitely not a png", languages=("eng",))
        assert result.error or not result.text.strip()
