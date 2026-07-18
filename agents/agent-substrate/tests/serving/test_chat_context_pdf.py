"""_extract_pdf_text / _build_file_context — PDF attachments must be
inlined as real extracted text, not left as metadata-only "attachments"
the model can't read.

Regression coverage for a real gap: PDF uploads used to always fall into
the metadata-only bucket (same as .docx/.zip/etc.), so the model could
never answer "what's in this file" without the user pasting the text
themselves — see routes/chat_context.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from substrate.serving.monolith.routes.chat_context import (
    _build_file_context,
    _extract_pdf_text,
)

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "test_invoice.pdf"


async def test_extract_pdf_text_returns_real_content():
    data = _FIXTURE.read_bytes()
    text = await _extract_pdf_text(data, "invoice.pdf")

    assert text is not None
    assert len(text) > 0


async def test_extract_pdf_text_truncates_over_configured_cap(monkeypatch):
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(chat_context.settings, "ATTACHMENT_PDF_MAX_CHARS", 5)
    data = _FIXTURE.read_bytes()
    text = await _extract_pdf_text(data, "invoice.pdf")

    assert text is not None
    assert text.startswith(text[:5])
    assert "truncated" in text


async def test_extract_pdf_text_returns_none_for_garbage_bytes():
    text = await _extract_pdf_text(b"not a real pdf", "bad.pdf")
    assert text is None


async def test_build_file_context_inlines_pdf_as_text():
    """End-to-end through _build_file_context: a PDF attachment must land
    in the returned text block, not the attachments-metadata list."""
    file_id = "11111111-1111-1111-1111-111111111111"
    meta = MagicMock()
    meta.id = file_id
    meta.original_name = "invoice.pdf"
    meta.content_type = "application/pdf"
    meta.size_bytes = _FIXTURE.stat().st_size
    meta.object_key = f"users/u1/uploads/{file_id}/invoice.pdf"

    scalars_result = MagicMock()
    scalars_result.all.return_value = [meta]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result

    db = MagicMock()
    db.execute = AsyncMock(return_value=execute_result)

    file_store = MagicMock()
    file_store.download = AsyncMock(return_value=_FIXTURE.read_bytes())

    ctx = MagicMock()
    ctx.file_store = file_store

    body = MagicMock()
    body.file_ids = [file_id]

    text_block, image_inputs, attachments = await _build_file_context(
        db, body, request=MagicMock(), ctx=ctx
    )

    assert "invoice.pdf" in text_block
    assert len(text_block) > len("[File: invoice.pdf]\n")
    assert image_inputs == []
    assert attachments == []


async def test_build_file_context_falls_back_to_attachment_on_bad_pdf():
    file_id = "22222222-2222-2222-2222-222222222222"
    meta = MagicMock()
    meta.id = file_id
    meta.original_name = "corrupt.pdf"
    meta.content_type = "application/pdf"
    meta.size_bytes = 12
    meta.object_key = f"users/u1/uploads/{file_id}/corrupt.pdf"

    scalars_result = MagicMock()
    scalars_result.all.return_value = [meta]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result

    db = MagicMock()
    db.execute = AsyncMock(return_value=execute_result)

    file_store = MagicMock()
    file_store.download = AsyncMock(return_value=b"not a real pdf")

    ctx = MagicMock()
    ctx.file_store = file_store

    body = MagicMock()
    body.file_ids = [file_id]

    text_block, image_inputs, attachments = await _build_file_context(
        db, body, request=MagicMock(), ctx=ctx
    )

    assert text_block == ""
    assert image_inputs == []
    assert len(attachments) == 1
    assert attachments[0]["name"] == "corrupt.pdf"
