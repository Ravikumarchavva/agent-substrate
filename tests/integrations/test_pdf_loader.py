"""PDFLoader — extraction-service-first, pdfplumber/pypdf-fallback
orchestration, pushed down into the loader itself (see backends/local.py's
own two-tier pattern this mirrors)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

from substrate.capabilities.knowledge.loaders.pdf_loader import PDFLoader
from substrate.runtimes.document_intelligence.client import (
    ExtractedPageText,
    ExtractResponse,
)

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "test_invoice.pdf"


async def test_load_without_client_uses_pdfplumber():
    loader = PDFLoader()
    docs = await loader.load(_FIXTURE.read_bytes(), metadata={"source": "invoice.pdf"})

    assert len(docs) >= 1
    assert docs[0].metadata.get("engine") is None  # pdfplumber path, no "engine" tag


async def test_load_with_client_uses_extraction_service():
    client = AsyncMock()
    client.extract = AsyncMock(
        return_value=ExtractResponse(
            success=True,
            text="page1",
            pages=[ExtractedPageText(page_number=1, text="page1")],
            engine="paddleocr",
            page_count=1,
        )
    )
    loader = PDFLoader(extraction_client=client)

    docs = await loader.load(_FIXTURE.read_bytes(), metadata={"source": "invoice.pdf"})

    client.extract.assert_awaited_once()
    assert len(docs) == 1
    assert docs[0].content[0].text == "page1"
    assert docs[0].metadata["engine"] == "paddleocr"


async def test_load_falls_back_to_pdfplumber_when_service_fails():
    client = AsyncMock()
    client.extract = AsyncMock(
        return_value=ExtractResponse(success=False, error="connection refused")
    )
    loader = PDFLoader(extraction_client=client)

    docs = await loader.load(_FIXTURE.read_bytes(), metadata={"source": "invoice.pdf"})

    client.extract.assert_awaited_once()
    assert len(docs) >= 1
    # Fell through to the local path — no "engine" tag from the service.
    assert docs[0].metadata.get("engine") is None
