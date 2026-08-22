"""routes/files.py::upload_file — the new upload-time caps (page count, size,
upload-attempt quota) and eager staged-ingestion trigger for RAG-eligible
docs. Calls the route function directly with mocked dependencies, same style
as test_chat_context_pdf.py — no full TestClient/DB needed for this logic.
"""

from __future__ import annotations

import io
from unittest.mock import AsyncMock, MagicMock

from PIL import Image

from substrate.serving.monolith.routes.files import get_doc_quota_status, upload_file

_PDF_CONTENT_TYPE = "application/pdf"


def _pdf_bytes(pages: int) -> bytes:
    images = [Image.new("RGB", (32, 32), color="white") for _ in range(pages)]
    buf = io.BytesIO()
    images[0].save(buf, format="PDF", save_all=True, append_images=images[1:])
    return buf.getvalue()


def _upload_file_mock(
    data: bytes, content_type: str, filename: str = "doc.pdf"
) -> MagicMock:
    f = MagicMock()
    f.read = AsyncMock(return_value=data)
    f.content_type = content_type
    f.filename = filename
    return f


def _db_mock() -> MagicMock:
    """No existing object_key collision, and add/commit/refresh no-ops."""
    db = MagicMock()
    scalar_result = MagicMock()
    scalar_result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=scalar_result)
    db.add = MagicMock()
    db.commit = AsyncMock()

    async def _refresh(meta):
        import uuid

        if meta.id is None:
            meta.id = uuid.uuid4()

    db.refresh = AsyncMock(side_effect=_refresh)
    return db


def _ctx_mock(*, rag_backend=None, redis=None) -> MagicMock:
    ctx = MagicMock()
    ctx.file_store = MagicMock()
    ctx.file_store.upload = AsyncMock()
    ctx.rag_backend = rag_backend
    ctx.session_factory = MagicMock()
    return ctx


def _request_mock(redis=None) -> MagicMock:
    request = MagicMock()
    request.app.state.redis = redis
    return request


def _claims_mock(sub: str = "test-user") -> MagicMock:
    claims = MagicMock()
    claims.sub = sub
    claims.tenant_id = None
    claims.email = "test@example.com"
    return claims


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def incrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) + amount
        return self.store[key]

    async def expire(self, key: str, seconds: int) -> None:
        pass

    async def get(self, key: str):
        val = self.store.get(key)
        return str(val) if val is not None else None


async def test_upload_rejects_pdf_over_page_limit(monkeypatch):
    from substrate.serving.monolith.routes import files as files_module

    monkeypatch.setattr(files_module.settings, "RAG_MAX_DOC_PAGES", 20)
    rag_backend = MagicMock()
    rag_backend.name = "local"
    data = _pdf_bytes(21)

    exc = None
    try:
        await upload_file(
            request=_request_mock(_FakeRedis()),
            file=_upload_file_mock(data, _PDF_CONTENT_TYPE),
            thread_id=None,
            claims=_claims_mock(),
            db=_db_mock(),
            ctx=_ctx_mock(rag_backend=rag_backend),
        )
    except Exception as e:
        exc = e

    assert exc is not None
    assert getattr(exc, "status_code", None) == 422


async def test_upload_allows_pdf_at_page_limit(monkeypatch):
    from substrate.serving.monolith.routes import files as files_module

    monkeypatch.setattr(files_module.settings, "RAG_MAX_DOC_PAGES", 20)
    rag_backend = MagicMock()
    rag_backend.name = "local"
    rag_backend.ingest = AsyncMock()
    data = _pdf_bytes(20)

    result = await upload_file(
        request=_request_mock(_FakeRedis()),
        file=_upload_file_mock(data, _PDF_CONTENT_TYPE),
        thread_id=None,
        claims=_claims_mock(),
        db=_db_mock(),
        ctx=_ctx_mock(rag_backend=rag_backend),
    )
    assert result.name == "doc.pdf"


async def test_upload_rejects_doc_over_size_limit(monkeypatch):
    from substrate.serving.monolith.routes import files as files_module

    monkeypatch.setattr(files_module.settings, "RAG_MAX_DOC_MB", 1)
    rag_backend = MagicMock()
    rag_backend.name = "local"
    oversized = b"x" * (2 * 1024 * 1024)  # 2MB, over the 1MB test cap

    exc = None
    try:
        await upload_file(
            request=_request_mock(_FakeRedis()),
            file=_upload_file_mock(oversized, _PDF_CONTENT_TYPE),
            thread_id=None,
            claims=_claims_mock(),
            db=_db_mock(),
            ctx=_ctx_mock(rag_backend=rag_backend),
        )
    except Exception as e:
        exc = e

    assert exc is not None
    assert getattr(exc, "status_code", None) == 413


async def test_upload_rejects_when_upload_attempt_quota_exhausted(monkeypatch):
    from substrate.serving.monolith.routes import files as files_module

    monkeypatch.setattr(files_module.settings, "RAG_DAILY_UPLOAD_ATTEMPT_LIMIT", 1)
    rag_backend = MagicMock()
    rag_backend.name = "local"
    redis = _FakeRedis()
    data = _pdf_bytes(1)

    # First upload consumes the only slot.
    await upload_file(
        request=_request_mock(redis),
        file=_upload_file_mock(data, _PDF_CONTENT_TYPE),
        thread_id=None,
        claims=_claims_mock(),
        db=_db_mock(),
        ctx=_ctx_mock(rag_backend=rag_backend),
    )

    exc = None
    try:
        await upload_file(
            request=_request_mock(redis),
            file=_upload_file_mock(data, _PDF_CONTENT_TYPE),
            thread_id=None,
            claims=_claims_mock(),
            db=_db_mock(),
            ctx=_ctx_mock(rag_backend=rag_backend),
        )
    except Exception as e:
        exc = e

    assert exc is not None
    assert getattr(exc, "status_code", None) == 429


async def test_upload_non_extractable_type_skips_all_new_checks(monkeypatch):
    """A non-PDF upload (e.g. a spreadsheet) must be completely unaffected —
    no page/size/quota checks, no eager staging — same as before this
    feature existed."""
    from substrate.serving.monolith.routes import files as files_module

    monkeypatch.setattr(files_module.settings, "RAG_MAX_DOC_MB", 1)
    rag_backend = MagicMock()
    rag_backend.name = "local"
    oversized = b"x" * (5 * 1024 * 1024)  # would fail the 1MB PDF cap, but isn't a PDF

    result = await upload_file(
        request=_request_mock(_FakeRedis()),
        file=_upload_file_mock(
            oversized, "application/vnd.ms-excel", filename="data.xlsx"
        ),
        thread_id=None,
        claims=_claims_mock(),
        db=_db_mock(),
        ctx=_ctx_mock(rag_backend=rag_backend),
    )
    assert result.name == "data.xlsx"
    rag_backend.ingest.assert_not_called()


async def test_upload_pinecone_backend_skips_upload_attempt_quota(monkeypatch):
    """The upload-attempt quota specifically bounds eager-staging compute
    abuse — Pinecone never stages eagerly, so it shouldn't be gated by it."""
    from substrate.serving.monolith.routes import files as files_module

    monkeypatch.setattr(files_module.settings, "RAG_DAILY_UPLOAD_ATTEMPT_LIMIT", 1)
    rag_backend = MagicMock()
    rag_backend.name = "pinecone"
    redis = _FakeRedis()
    data = _pdf_bytes(1)

    for _ in range(3):
        result = await upload_file(
            request=_request_mock(redis),
            file=_upload_file_mock(data, _PDF_CONTENT_TYPE),
            thread_id=None,
            claims=_claims_mock(),
            db=_db_mock(),
            ctx=_ctx_mock(rag_backend=rag_backend),
        )
        assert result.name == "doc.pdf"


async def test_upload_triggers_eager_staging_for_local_backend(monkeypatch):
    from substrate.serving.monolith.routes import files as files_module

    captured_coros = []
    monkeypatch.setattr(
        files_module.asyncio, "create_task", lambda coro: captured_coros.append(coro)
    )

    rag_backend = MagicMock()
    rag_backend.name = "local"
    rag_backend.ingest = AsyncMock()
    data = _pdf_bytes(1)

    await upload_file(
        request=_request_mock(_FakeRedis()),
        file=_upload_file_mock(data, _PDF_CONTENT_TYPE),
        thread_id=None,
        claims=_claims_mock(),
        db=_db_mock(),
        ctx=_ctx_mock(rag_backend=rag_backend),
    )

    assert len(captured_coros) == 1
    await captured_coros[0]  # run the staging task synchronously
    rag_backend.ingest.assert_awaited_once()
    _args, kwargs = rag_backend.ingest.call_args
    assert kwargs["collection"].startswith("staging:")


async def test_upload_writes_extracted_sidecar_for_pdf(monkeypatch):
    """After successful staging, a page-marked `.extracted.md` sidecar is
    written next to the original object, via the same file_store.upload
    path — so code_interpreter (which mounts the same session dir) can read
    it instead of re-parsing the PDF's raw bytes."""
    from substrate.serving.monolith.routes import files as files_module

    monkeypatch.setattr(files_module.settings, "DOC_HANDLER_SERVICE_URL", "")
    captured_coros = []
    monkeypatch.setattr(
        files_module.asyncio, "create_task", lambda coro: captured_coros.append(coro)
    )

    rag_backend = MagicMock()
    rag_backend.name = "local"
    rag_backend.ingest = AsyncMock()
    data = _pdf_bytes(2)
    ctx = _ctx_mock(rag_backend=rag_backend)

    await upload_file(
        request=_request_mock(_FakeRedis()),
        file=_upload_file_mock(data, _PDF_CONTENT_TYPE),
        thread_id=None,
        claims=_claims_mock(),
        db=_db_mock(),
        ctx=ctx,
    )

    assert len(captured_coros) == 1
    await captured_coros[0]  # run the staging task synchronously

    sidecar_calls = [
        call
        for call in ctx.file_store.upload.call_args_list
        if call.args[0] == "users/test-user/uploads/doc.pdf.extracted.md"
    ]
    assert len(sidecar_calls) == 1
    sidecar_text = sidecar_calls[0].args[1].decode("utf-8")
    assert "## Page 1" in sidecar_text
    assert "## Page 2" in sidecar_text
    assert sidecar_calls[0].kwargs["content_type"] == "text/markdown"


async def test_upload_sidecar_write_failure_does_not_fail_staging(monkeypatch):
    """The sidecar write is best-effort — a failure there must not surface
    as a staging_error on the file."""
    from substrate.serving.monolith.routes import files as files_module

    monkeypatch.setattr(files_module.settings, "DOC_HANDLER_SERVICE_URL", "")
    captured_coros = []
    monkeypatch.setattr(
        files_module.asyncio, "create_task", lambda coro: captured_coros.append(coro)
    )

    rag_backend = MagicMock()
    rag_backend.name = "local"
    rag_backend.ingest = AsyncMock()
    data = _pdf_bytes(1)
    ctx = _ctx_mock(rag_backend=rag_backend)

    async def _upload_side_effect(key, *_args, **_kwargs):
        if key.endswith(".extracted.md"):
            raise RuntimeError("boom")

    ctx.file_store.upload = AsyncMock(side_effect=_upload_side_effect)

    await upload_file(
        request=_request_mock(_FakeRedis()),
        file=_upload_file_mock(data, _PDF_CONTENT_TYPE),
        thread_id=None,
        claims=_claims_mock(),
        db=_db_mock(),
        ctx=ctx,
    )

    assert len(captured_coros) == 1
    await captured_coros[0]  # must not raise


async def test_upload_pinecone_backend_skips_eager_staging(monkeypatch):
    from substrate.serving.monolith.routes import files as files_module

    captured_coros = []
    monkeypatch.setattr(
        files_module.asyncio, "create_task", lambda coro: captured_coros.append(coro)
    )

    rag_backend = MagicMock()
    rag_backend.name = "pinecone"
    data = _pdf_bytes(1)

    await upload_file(
        request=_request_mock(_FakeRedis()),
        file=_upload_file_mock(data, _PDF_CONTENT_TYPE),
        thread_id=None,
        claims=_claims_mock(),
        db=_db_mock(),
        ctx=_ctx_mock(rag_backend=rag_backend),
    )

    assert captured_coros == []


# ── GET /files/quota/status ──────────────────────────────────────────────────


async def test_doc_quota_status_reports_zero_used_for_fresh_user(monkeypatch):
    from substrate.serving.monolith.routes import files as files_module

    monkeypatch.setattr(files_module.settings, "RAG_DAILY_DOC_LIMIT", 20)

    status = await get_doc_quota_status(
        request=_request_mock(_FakeRedis()), claims=_claims_mock()
    )

    assert status == {
        "enabled": True,
        "used": 0,
        "limit": 20,
        "reset_in": status["reset_in"],
    }
    assert status["reset_in"] > 0


async def test_doc_quota_status_reflects_prior_commits(monkeypatch):
    from datetime import datetime, timezone

    from substrate.serving.monolith.routes import files as files_module

    monkeypatch.setattr(files_module.settings, "RAG_DAILY_DOC_LIMIT", 20)
    redis = _FakeRedis()
    today = datetime.now(timezone.utc).date().isoformat()
    await redis.incrby(f"docquota:commit:test-user:{today}", 7)

    status = await get_doc_quota_status(
        request=_request_mock(redis), claims=_claims_mock()
    )

    assert status["used"] == 7
    assert status["limit"] == 20


async def test_doc_quota_status_disabled_without_redis():
    status = await get_doc_quota_status(
        request=_request_mock(None), claims=_claims_mock()
    )
    assert status["enabled"] is False
