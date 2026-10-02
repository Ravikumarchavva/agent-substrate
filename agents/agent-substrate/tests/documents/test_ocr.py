"""The OCR backends held to ``OcrConformance``; each is skipped where it is not installed."""

from __future__ import annotations

import pytest

from substrate.documents.reading.ocr import RapidOcr, TesseractOcr, resolve_ocr
from substrate.testing.conformance.ocr import OcrConformance
from tests.fixtures.pdfs import Line, Page, build


@pytest.fixture(scope="module")
def invoice_png() -> bytes:
    import pypdfium2 as pdfium

    from substrate.documents.reading.png import encode_png

    document = pdfium.PdfDocument(build([Page([Line("Invoice 4417 total due 120 EUR", size=24)])]))
    bitmap = document[0].render(scale=300 / 72, grayscale=True)
    pixels = bytes(bitmap.buffer)
    png = encode_png(bitmap.width, bitmap.height, pixels, stride=bitmap.stride)
    bitmap.close()
    document.close()
    return png


@pytest.mark.skipif(not TesseractOcr.available(), reason="tesseract is not installed")
class TestTesseract(OcrConformance):
    @pytest.fixture
    def ocr(self):
        return TesseractOcr()


@pytest.mark.skipif(not RapidOcr.available(), reason="rapidocr is not installed (the `ocr` extra)")
class TestRapidOcr(OcrConformance):
    @pytest.fixture
    def ocr(self):
        return RapidOcr()


def test_auto_prefers_rapidocr_then_tesseract_then_nothing(monkeypatch) -> None:
    monkeypatch.setattr(RapidOcr, "available", classmethod(lambda cls: False))
    monkeypatch.setattr(TesseractOcr, "available", classmethod(lambda cls, binary=None: False))
    assert resolve_ocr("auto") is None and resolve_ocr(None) is None
    monkeypatch.setattr(TesseractOcr, "available", classmethod(lambda cls, binary=None: True))
    assert resolve_ocr("auto").name == "tesseract"
    monkeypatch.setattr(RapidOcr, "available", classmethod(lambda cls: True))
    assert resolve_ocr("auto").name == "rapidocr"
