"""PDFLoader — delegates entirely to an injected DocumentExtractor, defaulting
to LocalDocumentExtractor."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from substrate.integrations.knowledge.loaders.pdf_loader import PDFLoader
from substrate.kernel.document import ExtractedPage, ExtractionResult

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "test_invoice.pdf"


async def test_load_without_extractor_uses_local_default():
    loader = PDFLoader()
    docs = await loader.load(_FIXTURE.read_bytes(), metadata={"source": "invoice.pdf"})

    assert len(docs) >= 1
    assert docs[0].metadata.get("engine") == "local"


async def test_load_with_extractor_uses_injected_result():
    extractor = AsyncMock()
    extractor.extract = AsyncMock(
        return_value=ExtractionResult(
            success=True,
            pages=[ExtractedPage(page_number=1, text="page1")],
            engine="paddleocr",
        )
    )
    loader = PDFLoader(extractor=extractor)

    docs = await loader.load(_FIXTURE.read_bytes(), metadata={"source": "invoice.pdf"})

    extractor.extract.assert_awaited_once()
    assert len(docs) == 1
    assert docs[0].content[0].text == "page1"
    assert docs[0].metadata["engine"] == "paddleocr"


async def test_load_returns_empty_list_when_extraction_fails():
    extractor = AsyncMock()
    extractor.extract = AsyncMock(
        return_value=ExtractionResult(success=False, error="connection refused")
    )
    loader = PDFLoader(extractor=extractor)

    docs = await loader.load(_FIXTURE.read_bytes(), metadata={"source": "invoice.pdf"})

    extractor.extract.assert_awaited_once()
    assert docs == []
