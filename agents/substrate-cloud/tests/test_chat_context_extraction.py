"""chat_context's wiring around ``document_reader()`` — the ``Reader`` is built from settings, and the returned ``ExtractionResult`` is
turned into the ``(text, engine)`` the rest of ``_build_file_context`` expects. How the ``Reader`` reads (service, built-in, fallback) is
the library's own test suite; here it is stubbed at chat_context's import site."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from substrate.documents import ExtractionResult
from substrate_cloud.monolith.routes.chat_context import _build_file_context


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

    # Request code reaches the stores through the tenant fence; the fake hands back the same fakes.

    ctx.files_for = lambda _tenant, ctx=ctx: ctx.file_store

    ctx.pending_for = lambda _tenant, ctx=ctx: ctx.pending_file_store
    ctx.file_store = file_store
    ctx.rag_backend = None  # exercises the inline-extraction path
    ctx.library = None

    body = MagicMock()
    body.file_ids = [meta.id]

    return await _build_file_context(db, body, MagicMock(), ctx, MagicMock())


class _Reader:
    """Stands in for the ``Reader`` ``document_reader()`` builds: records the call, answers with ``result``."""

    def __init__(self, result: ExtractionResult) -> None:
        self.read = AsyncMock(return_value=result)


def test_the_document_reader_is_built_from_settings(monkeypatch):
    from substrate_cloud import document_reader as module

    monkeypatch.setattr(
        module.settings,
        "DOCUMENT_INTELLIGENCE_SERVICE_URL",
        "http://extraction-test:8080",
    )
    monkeypatch.setattr(
        module.settings, "DOCUMENT_INTELLIGENCE_AUTH_TOKEN", "secret-token"
    )
    monkeypatch.setattr(module.settings, "DOCUMENT_INTELLIGENCE_TIMEOUT_S", 42)
    reader = module.document_reader()
    assert (reader._location, reader._api_key, reader._timeout) == (
        "http://extraction-test:8080",
        "secret-token",
        42.0,
    )


def test_with_no_service_configured_the_built_in_reader_is_used(monkeypatch):
    from substrate_cloud import document_reader as module

    monkeypatch.setattr(module.settings, "DOCUMENT_INTELLIGENCE_SERVICE_URL", "")
    assert module.document_reader()._location is None


async def test_extraction_result_becomes_the_inline_text_and_engine():
    from substrate_cloud.monolith.routes import chat_context

    meta = _pdf_meta("f1", "invoice.pdf", "users/u1/uploads/f1/invoice.pdf", 1234)
    reader = _Reader(
        ExtractionResult(
            pages=[], markdown="rich layout-aware text", engine="paddleocr-vl"
        )
    )
    with patch.object(chat_context, "document_reader", lambda: reader):
        text_block, _images, attachments, _new = await _run(meta)

    assert reader.read.await_args.args == (b"pdf bytes", "invoice.pdf")
    assert reader.read.await_args.kwargs["content_type"] == "application/pdf"
    assert "rich layout-aware text" in text_block
    assert meta.extraction_engine == "paddleocr-vl"
    assert len(attachments) == 1


async def test_extraction_empty_markdown_falls_back_to_attachment_metadata():
    """An ``ExtractionResult`` with blank markdown (e.g. a scanned/empty
    document neither the service nor the local engine could read anything
    from) must fall through to metadata-only attachment handling, not be
    cached as if it were real content."""
    from substrate_cloud.monolith.routes import chat_context

    meta = _pdf_meta("f3", "corrupt.pdf", "users/u1/uploads/f3/corrupt.pdf", 12)
    reader = _Reader(ExtractionResult(pages=[], markdown="   ", engine="raw_text"))
    with patch.object(chat_context, "document_reader", lambda: reader):
        text_block, _images, attachments, _new = await _run(meta)

    assert text_block == ""
    assert meta.extracted_text is None
    assert len(attachments) == 1
    assert attachments[0]["name"] == "corrupt.pdf"


async def test_extraction_truncates_over_configured_cap(monkeypatch):
    from substrate_cloud.monolith.routes import chat_context

    monkeypatch.setattr(chat_context.settings, "ATTACHMENT_PDF_MAX_CHARS", 5)

    meta = _pdf_meta("f4", "invoice.pdf", "users/u1/uploads/f4/invoice.pdf", 1234)
    reader = _Reader(
        ExtractionResult(
            pages=[], markdown="a much longer body of extracted text", engine="raw_text"
        )
    )
    with patch.object(chat_context, "document_reader", lambda: reader):
        text_block, _images, _attachments, _new = await _run(meta)

    assert "truncated" in text_block
    assert meta.extraction_engine == "raw_text"
