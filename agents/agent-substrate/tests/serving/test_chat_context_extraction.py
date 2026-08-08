"""_extract_document_text — docling-first, pypdf-fallback orchestration.

Covers the paths test_chat_context_pdf.py doesn't: docling configured and
succeeding (including DOCX/PPTX, which pypdf can never read), and docling
configured but failing for a PDF (must fall back to pypdf rather than
losing the attachment entirely)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from substrate.capabilities.knowledge.docling_client import DoclingExtractResponse
from substrate.serving.monolith.routes.chat_context import _extract_document_text

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "test_invoice.pdf"


def _mock_docling_client(response: DoclingExtractResponse):
    """Patch chat_context's local DoclingClient import to return a client
    whose .extract() always returns `response`, without touching the
    network."""
    instance = AsyncMock()
    instance.extract = AsyncMock(return_value=response)
    instance.close = AsyncMock()
    return patch(
        "substrate.capabilities.knowledge.docling_client.DoclingClient",
        return_value=instance,
    )


async def test_docling_configured_and_succeeds_for_pdf(monkeypatch):
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(
        chat_context.settings, "DOCLING_SERVICE_URL", "http://docling-test:8080"
    )

    with _mock_docling_client(
        DoclingExtractResponse(
            success=True, text="rich table-aware text", engine="docling"
        )
    ):
        text, engine = await _extract_document_text(
            _FIXTURE.read_bytes(), "invoice.pdf", "application/pdf"
        )

    assert text == "rich table-aware text"
    assert engine == "docling"


async def test_docling_configured_and_succeeds_for_docx(monkeypatch):
    """The one thing pypdf genuinely cannot do — docling is the only path
    that can ever return text for a DOCX attachment."""
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(
        chat_context.settings, "DOCLING_SERVICE_URL", "http://docling-test:8080"
    )

    with _mock_docling_client(
        DoclingExtractResponse(success=True, text="docx contents", engine="docling")
    ):
        text, engine = await _extract_document_text(
            b"fake docx bytes",
            "report.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    assert text == "docx contents"
    assert engine == "docling"


async def test_docling_configured_but_fails_falls_back_to_pypdf_for_pdf(monkeypatch):
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(
        chat_context.settings, "DOCLING_SERVICE_URL", "http://docling-test:8080"
    )

    with _mock_docling_client(
        DoclingExtractResponse(success=False, error="Connection error: refused")
    ):
        text, engine = await _extract_document_text(
            _FIXTURE.read_bytes(), "invoice.pdf", "application/pdf"
        )

    assert text is not None
    assert engine == "pypdf"


async def test_docling_configured_but_fails_for_docx_returns_none(monkeypatch):
    """No local fallback exists for DOCX — a docling failure there is a
    real, honest failure, not a silent downgrade to garbage text."""
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(
        chat_context.settings, "DOCLING_SERVICE_URL", "http://docling-test:8080"
    )

    with _mock_docling_client(
        DoclingExtractResponse(success=False, error="Timeout: read timed out")
    ):
        text, engine = await _extract_document_text(
            b"fake docx bytes",
            "report.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )

    assert text is None
    assert engine is None


async def test_docling_empty_text_success_treated_as_failure(monkeypatch):
    """success=True with blank text (e.g. a scanned page docling couldn't
    OCR) must still fall back for PDFs rather than caching an empty
    string as if it were real content."""
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(
        chat_context.settings, "DOCLING_SERVICE_URL", "http://docling-test:8080"
    )

    with _mock_docling_client(
        DoclingExtractResponse(success=True, text="   ", engine="docling")
    ):
        text, engine = await _extract_document_text(
            _FIXTURE.read_bytes(), "invoice.pdf", "application/pdf"
        )

    assert text is not None
    assert engine == "pypdf"
