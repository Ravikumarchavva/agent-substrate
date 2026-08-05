"""_extract_via_pypdf / _build_file_context — PDF attachments must be
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
    _extract_document_text,
    _extract_via_pypdf,
)

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "test_invoice.pdf"


def _pdf_meta(file_id: str, name: str, object_key: str, size: int) -> MagicMock:
    """A FileMetadata mock with a clean (no-cache) initial state.

    MagicMock auto-creates truthy attributes on access, so leaving
    extracted_text unset would make `if meta.extracted_text:` incorrectly
    look like a cache hit — every test needs this explicit reset.
    """
    meta = MagicMock()
    meta.id = file_id
    meta.original_name = name
    meta.content_type = "application/pdf"
    meta.size_bytes = size
    meta.object_key = object_key
    meta.extracted_text = None
    meta.extracted_at = None
    meta.extraction_engine = None
    return meta


async def test_extract_via_pypdf_returns_real_content():
    data = _FIXTURE.read_bytes()
    text = await _extract_via_pypdf(data, "invoice.pdf")

    assert text is not None
    assert len(text) > 0


async def test_extract_via_pypdf_returns_none_for_garbage_bytes():
    text = await _extract_via_pypdf(b"not a real pdf", "bad.pdf")
    assert text is None


async def test_extract_document_text_truncates_over_configured_cap(monkeypatch):
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(chat_context.settings, "DOCLING_SERVICE_URL", "")
    monkeypatch.setattr(chat_context.settings, "ATTACHMENT_PDF_MAX_CHARS", 5)
    data = _FIXTURE.read_bytes()
    text, engine = await _extract_document_text(data, "invoice.pdf", "application/pdf")

    assert text is not None
    assert engine == "pypdf"
    assert "truncated" in text


async def test_extract_document_text_no_docling_configured_uses_pypdf_for_pdf(
    monkeypatch,
):
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(chat_context.settings, "DOCLING_SERVICE_URL", "")
    data = _FIXTURE.read_bytes()
    text, engine = await _extract_document_text(data, "invoice.pdf", "application/pdf")

    assert text is not None
    assert engine == "pypdf"


async def test_extract_document_text_docx_without_docling_returns_none():
    """No pypdf equivalent exists for DOCX — without a docling service
    configured this must fail cleanly, not raise."""
    text, engine = await _extract_document_text(
        b"fake docx bytes",
        "report.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert text is None
    assert engine is None


async def test_build_file_context_inlines_pdf_as_text(monkeypatch):
    """End-to-end through _build_file_context: a PDF attachment must land
    in the returned text block *and* still get an attachment record (the
    UI/history needs the latter regardless of extraction outcome)."""
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(chat_context.settings, "DOCLING_SERVICE_URL", "")
    file_id = "11111111-1111-1111-1111-111111111111"
    meta = _pdf_meta(
        file_id,
        "invoice.pdf",
        f"users/u1/uploads/{file_id}/invoice.pdf",
        _FIXTURE.stat().st_size,
    )

    scalars_result = MagicMock()
    scalars_result.all.return_value = [meta]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result

    db = MagicMock()
    db.execute = AsyncMock(return_value=execute_result)
    db.commit = AsyncMock()

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
    # A successfully-extracted file still gets an attachment record so the
    # UI can render/persist the attachment card — extraction only controls
    # what the model sees inline, not whether the file was "attached".
    assert len(attachments) == 1
    assert attachments[0]["name"] == "invoice.pdf"

    # Extraction cache must be written back onto the row.
    assert meta.extracted_text is not None
    assert meta.extraction_engine == "pypdf"
    assert meta.extracted_at is not None
    db.commit.assert_awaited_once()


async def test_build_file_context_falls_back_to_attachment_on_bad_pdf():
    file_id = "22222222-2222-2222-2222-222222222222"
    meta = _pdf_meta(
        file_id, "corrupt.pdf", f"users/u1/uploads/{file_id}/corrupt.pdf", 12
    )

    scalars_result = MagicMock()
    scalars_result.all.return_value = [meta]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result

    db = MagicMock()
    db.execute = AsyncMock(return_value=execute_result)
    db.commit = AsyncMock()

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
    db.commit.assert_not_awaited()


def _xlsx_meta(file_id: str, name: str, object_key: str, size: int) -> MagicMock:
    """A non-extractable-type FileMetadata mock — goes straight to the
    generic attachment path in _build_file_context, isolating workspace_path
    computation from PDF-extraction branching."""
    meta = MagicMock()
    meta.id = file_id
    meta.original_name = name
    meta.content_type = (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    meta.size_bytes = size
    meta.object_key = object_key
    meta.extracted_text = None
    meta.extracted_at = None
    meta.extraction_engine = None
    return meta


async def _run_build_file_context_for_workspace_path(object_key: str):
    file_id = "44444444-4444-4444-4444-444444444444"
    meta = _xlsx_meta(file_id, "data.xlsx", object_key, 1234)

    scalars_result = MagicMock()
    scalars_result.all.return_value = [meta]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result

    db = MagicMock()
    db.execute = AsyncMock(return_value=execute_result)
    db.commit = AsyncMock()

    ctx = MagicMock()
    ctx.file_store = MagicMock()

    body = MagicMock()
    body.file_ids = [file_id]

    _text, _images, attachments = await _build_file_context(
        db, body, request=MagicMock(), ctx=ctx
    )
    assert len(attachments) == 1
    return attachments[0]


async def test_workspace_path_strips_session_prefix_for_bubblewrap_mode(monkeypatch):
    """Bubblewrap mounts ONLY the caller's own session dir (see
    CodeInterpreterTool._session_dir) at /workspace — the full
    users/{uid}/sessions/{tid}/ prefix must be stripped, not just users/{uid}/."""
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(chat_context.settings, "SANDBOX_RUNTIME", "bubblewrap")
    monkeypatch.setattr(chat_context.settings, "CI_WORKSPACE_PVC_CLAIM", "")

    attachment = await _run_build_file_context_for_workspace_path(
        "users/u1/sessions/t1/data.xlsx"
    )
    assert attachment["workspace_path"] == "/workspace/data.xlsx"


async def test_workspace_path_strips_user_prefix_for_k8s_pvc_mode(monkeypatch):
    """K8s agent-sandbox subPath-mounts users/{uid} at /app/workspace (the
    subPath IS the per-user isolation boundary), so the prefix is stripped
    before being made absolute — the sandbox's execution cwd isn't
    guaranteed to be the workspace root (sandbox_runtime.py changes cwd to
    sessions/{session_id} per run), so a relative path would be wrong."""
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(chat_context.settings, "SANDBOX_RUNTIME", "k8s")
    monkeypatch.setattr(
        chat_context.settings, "CI_WORKSPACE_PVC_CLAIM", "workspace-pvc"
    )

    attachment = await _run_build_file_context_for_workspace_path(
        "users/u1/sessions/t1/data.xlsx"
    )
    assert attachment["workspace_path"] == "/app/workspace/sessions/t1/data.xlsx"


async def test_workspace_path_absent_when_no_sandbox_configured(monkeypatch):
    from substrate.serving.monolith.routes import chat_context

    monkeypatch.setattr(chat_context.settings, "SANDBOX_RUNTIME", "inprocess")
    monkeypatch.setattr(chat_context.settings, "CI_WORKSPACE_PVC_CLAIM", "")

    attachment = await _run_build_file_context_for_workspace_path(
        "users/u1/sessions/t1/data.xlsx"
    )
    assert "workspace_path" not in attachment


async def test_build_file_context_uses_cached_extracted_text_without_download():
    """A file that was already extracted (extracted_text set) must skip
    both the file-store download and re-extraction entirely."""
    file_id = "33333333-3333-3333-3333-333333333333"
    meta = _pdf_meta(
        file_id, "invoice.pdf", f"users/u1/uploads/{file_id}/invoice.pdf", 1234
    )
    meta.extracted_text = "cached invoice contents"
    meta.extraction_engine = "pypdf"

    scalars_result = MagicMock()
    scalars_result.all.return_value = [meta]
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result

    db = MagicMock()
    db.execute = AsyncMock(return_value=execute_result)
    db.commit = AsyncMock()

    file_store = MagicMock()
    file_store.download = AsyncMock(
        side_effect=AssertionError("must not download on cache hit")
    )

    ctx = MagicMock()
    ctx.file_store = file_store

    body = MagicMock()
    body.file_ids = [file_id]

    text_block, _images, attachments = await _build_file_context(
        db, body, request=MagicMock(), ctx=ctx
    )

    assert "cached invoice contents" in text_block
    assert len(attachments) == 1
    assert attachments[0]["name"] == "invoice.pdf"
    file_store.download.assert_not_awaited()
    db.commit.assert_not_awaited()
