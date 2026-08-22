"""_extract_document_text — extraction-service-first, pypdf-fallback
orchestration.

Covers the paths test_chat_context_pdf.py doesn't: the extraction service
configured and succeeding, and configured but failing for a PDF (must fall
back to pypdf rather than losing the attachment entirely). DOCX/PPTX is not
covered here — PaddleOCR has no reader for those formats, so there's no
extraction path for them at all (same limitation the local pypdf fallback
already has)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from substrate.doc_handler.client import ExtractResponse
from substrate.serving.monolith.routes.chat_context import _extract_document_text

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "test_invoice.pdf"


def _mock_extraction_client(response: ExtractResponse):
    """Patch chat_context's local ExtractionClient import to return a client
    whose .extract() always returns `response`, without touching the
    network."""
    instance = AsyncMock()
    instance.extract = AsyncMock(return_value=response)
    instance.close = AsyncMock()
    return patch(
        "substrate.doc_handler.client.ExtractionClient",
        return_value=instance,
    )


async def test_extraction_configured_and_succeeds_for_pdf(monkeypatch):
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(
        chat_context.settings, "DOC_HANDLER_SERVICE_URL", "http://extraction-test:8080"
    )

    with _mock_extraction_client(
        ExtractResponse(success=True, text="rich layout-aware text", engine="paddleocr")
    ):
        text, engine = await _extract_document_text(
            _FIXTURE.read_bytes(), "invoice.pdf", "application/pdf"
        )

    assert text == "rich layout-aware text"
    assert engine == "paddleocr"


async def test_extraction_configured_but_fails_falls_back_to_pypdf_for_pdf(
    monkeypatch,
):
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(
        chat_context.settings, "DOC_HANDLER_SERVICE_URL", "http://extraction-test:8080"
    )

    with _mock_extraction_client(
        ExtractResponse(success=False, error="Connection error: refused")
    ):
        text, engine = await _extract_document_text(
            _FIXTURE.read_bytes(), "invoice.pdf", "application/pdf"
        )

    assert text is not None
    assert engine == "pypdf"


async def test_extraction_empty_text_success_treated_as_failure(monkeypatch):
    """success=True with blank text (e.g. a scanned page PaddleOCR couldn't
    read) must still fall back for PDFs rather than caching an empty string
    as if it were real content."""
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(
        chat_context.settings, "DOC_HANDLER_SERVICE_URL", "http://extraction-test:8080"
    )

    with _mock_extraction_client(
        ExtractResponse(success=True, text="   ", engine="paddleocr")
    ):
        text, engine = await _extract_document_text(
            _FIXTURE.read_bytes(), "invoice.pdf", "application/pdf"
        )

    assert text is not None
    assert engine == "pypdf"
