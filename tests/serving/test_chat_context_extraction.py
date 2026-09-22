"""chat_context's own wiring around the shared ``extract_document`` —
endpoint construction from settings, and how the returned
``ExtractionResult`` is turned into the ``(text, engine)`` the rest of
``_build_file_context`` expects.

The service-vs-local fallback decision tree itself now lives in
``runtimes/document_intelligence/extract.py`` and is covered there (see
``tests/document_intelligence/test_extract.py``) — these tests only pin
that chat_context builds the right ``InferenceEndpoint`` and consumes the
result correctly, via ``AsyncMock``-patching ``extract_document`` at its
chat_context import site rather than re-exercising the extraction
service/pypdf fallback logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from substrate.kernel.document import ExtractionResult
from substrate.serving.monolith.routes.chat_context import _build_file_context


def _pdf_meta(file_id: str, name: str, object_key: str, size: int) -> MagicMock:
    meta = MagicMock()
    meta.id = file_id
    meta.original_name = name
    meta.content_type = "application/pdf"
    meta.size_bytes = size
    meta.object_key = object_key
    meta.extracted_text = None
    meta.extracted_at = None
    meta.extraction_engine = None
    meta.rag_ingested_at = None
    return meta


async def _run(meta) -> tuple[str, list, list, list]:
    scalars_result = MagicMock()
    scalars_result.all.return_value = [meta]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result

    db = MagicMock()
    db.execute = AsyncMock(return_value=execute_result)
    db.commit = AsyncMock()

    file_store = MagicMock()
    file_store.download = AsyncMock(return_value=b"pdf bytes")

    ctx = MagicMock()
    ctx.file_store = file_store
    ctx.rag_backend = None  # exercises the inline-extraction path

    body = MagicMock()
    body.file_ids = [meta.id]

    return await _build_file_context(db, body, MagicMock(), ctx, MagicMock())


async def test_extraction_configured_builds_endpoint_from_settings(monkeypatch):
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(
        chat_context.settings,
        "DOCUMENT_INTELLIGENCE_SERVICE_URL",
        "http://extraction-test:8080",
    )
    monkeypatch.setattr(
        chat_context.settings, "DOCUMENT_INTELLIGENCE_AUTH_TOKEN", "secret-token"
    )
    monkeypatch.setattr(chat_context.settings, "DOCUMENT_INTELLIGENCE_TIMEOUT_S", 42.0)

    meta = _pdf_meta("f1", "invoice.pdf", "users/u1/uploads/f1/invoice.pdf", 1234)
    mock_extract = AsyncMock(
        return_value=ExtractionResult(
            pages=[], markdown="rich layout-aware text", engine="paddleocr-vl"
        )
    )
    with patch.object(chat_context, "extract_document", mock_extract):
        text_block, _images, attachments, _new = await _run(meta)

    assert mock_extract.await_args.kwargs["endpoint"].base_url == "http://extraction-test:8080"
    assert mock_extract.await_args.kwargs["endpoint"].api_key == "secret-token"
    assert mock_extract.await_args.kwargs["endpoint"].timeout_s == 42.0
    assert "rich layout-aware text" in text_block
    assert meta.extraction_engine == "paddleocr-vl"
    assert len(attachments) == 1


async def test_extraction_not_configured_passes_no_endpoint(monkeypatch):
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(chat_context.settings, "DOCUMENT_INTELLIGENCE_SERVICE_URL", "")

    meta = _pdf_meta("f2", "invoice.pdf", "users/u1/uploads/f2/invoice.pdf", 1234)
    mock_extract = AsyncMock(
        return_value=ExtractionResult(pages=[], markdown="local text", engine="raw_text")
    )
    with patch.object(chat_context, "extract_document", mock_extract):
        await _run(meta)

    assert mock_extract.await_args.kwargs["endpoint"] is None


async def test_extraction_empty_markdown_falls_back_to_attachment_metadata():
    """An ``ExtractionResult`` with blank markdown (e.g. a scanned/empty
    document neither the service nor the local engine could read anything
    from) must fall through to metadata-only attachment handling, not be
    cached as if it were real content."""
    from substrate.serving.monolith.routes import chat_context

    meta = _pdf_meta("f3", "corrupt.pdf", "users/u1/uploads/f3/corrupt.pdf", 12)
    mock_extract = AsyncMock(
        return_value=ExtractionResult(pages=[], markdown="   ", engine="raw_text")
    )
    with patch.object(chat_context, "extract_document", mock_extract):
        text_block, _images, attachments, _new = await _run(meta)

    assert text_block == ""
    assert meta.extracted_text is None
    assert len(attachments) == 1
    assert attachments[0]["name"] == "corrupt.pdf"


async def test_extraction_truncates_over_configured_cap(monkeypatch):
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(chat_context.settings, "ATTACHMENT_PDF_MAX_CHARS", 5)

    meta = _pdf_meta("f4", "invoice.pdf", "users/u1/uploads/f4/invoice.pdf", 1234)
    mock_extract = AsyncMock(
        return_value=ExtractionResult(
            pages=[], markdown="a much longer body of extracted text", engine="raw_text"
        )
    )
    with patch.object(chat_context, "extract_document", mock_extract):
        text_block, _images, _attachments, _new = await _run(meta)

    assert "truncated" in text_block
    assert meta.extraction_engine == "raw_text"
