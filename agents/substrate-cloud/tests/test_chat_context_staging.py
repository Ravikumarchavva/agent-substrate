"""_build_file_context — the send-time pre-validation pass for eagerly staged documents: a chat send referencing a file whose staging
failed, is still in progress, or would exceed the daily commit quota is blocked entirely (not a silent per-file degrade — see the
plan's explicit "block the whole send" decision). A file already staged *for this exact thread* (already filed in the conversation's
documents) needs no further work; one staged under a different thread gets filed now, under this message's real thread_id."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from substrate_cloud.monolith.routes.chat_context import _build_file_context


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}

    async def incrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) + amount
        return self.store[key]

    async def decrby(self, key: str, amount: int) -> int:
        self.store[key] = self.store.get(key, 0) - amount
        return self.store[key]

    async def expire(self, key: str, seconds: int) -> None:
        pass

    async def get(self, key: str):
        val = self.store.get(key)
        return str(val) if val is not None else None

    async def set(self, key: str, value: int, *, keepttl: bool = False) -> None:
        self.store[key] = value


def _staged_meta(
    file_id: str,
    *,
    staged_at="2026-01-01T00:00:00Z",
    staging_error=None,
    rag_ingested_at=None,
    thread_id="thread-1",
) -> MagicMock:
    meta = MagicMock()
    meta.id = file_id
    meta.original_name = "invoice.pdf"
    meta.content_type = "application/pdf"
    meta.object_key = f"users/u1/uploads/{file_id}/invoice.pdf"
    meta.size_bytes = 1234
    meta.staged_at = staged_at
    meta.staging_error = staging_error
    meta.rag_ingested_at = rag_ingested_at
    # Matches body.thread_id in every test below by default — a file
    # already staged under *this* thread needs no further ingestion work.
    # Pass thread_id=None or a different value to exercise the "needs
    # (re-)ingestion" branch instead.
    meta.thread_id = thread_id
    return meta


def _db_with_rows(rows: list) -> MagicMock:
    scalars_result = MagicMock()
    scalars_result.all.return_value = rows
    execute_result = MagicMock()
    execute_result.scalars.return_value = scalars_result
    db = MagicMock()
    db.execute = AsyncMock(return_value=execute_result)
    db.commit = AsyncMock()
    return db


def _request_with_redis(redis) -> MagicMock:
    request = MagicMock()
    request.app.state.redis = redis
    return request


def _claims() -> MagicMock:
    return MagicMock(sub="user-1", tenant_id="t1")


def _ctx(*, download: bytes | None = None) -> MagicMock:
    """A ctx whose documents library is a stub: ``add`` records, ``outline`` says the document is not (yet) there."""
    ctx = MagicMock()
    # Request code reaches the stores through the tenant fence; the fake hands back the same fakes.
    ctx.files_for = lambda _tenant, ctx=ctx: ctx.file_store
    ctx.pending_for = lambda _tenant, ctx=ctx: ctx.pending_file_store
    ctx.file_store = MagicMock()
    if download is not None:
        ctx.file_store.download = AsyncMock(return_value=download)
    ctx.library = MagicMock()
    ctx.library.add = AsyncMock()
    ctx.library.outline = AsyncMock(return_value=None)
    ctx.library.document_id = MagicMock(return_value="invoice-abc123")
    return ctx


def _body(*file_ids: str) -> MagicMock:
    body = MagicMock()
    body.file_ids = list(file_ids)
    body.thread_id = "thread-1"
    return body


async def test_send_blocked_when_staging_still_in_progress():
    db = _db_with_rows([_staged_meta("f1", staged_at=None)])

    exc = None
    try:
        await _build_file_context(
            db, _body("f1"), _request_with_redis(_FakeRedis()), _ctx(), _claims()
        )
    except HTTPException as e:
        exc = e

    assert exc is not None
    assert exc.status_code == 425


async def test_send_blocked_when_staging_failed():
    db = _db_with_rows(
        [_staged_meta("f1", staged_at=None, staging_error="OCR crashed")]
    )

    exc = None
    try:
        await _build_file_context(
            db, _body("f1"), _request_with_redis(_FakeRedis()), _ctx(), _claims()
        )
    except HTTPException as e:
        exc = e

    assert exc is not None
    assert exc.status_code == 422
    assert "OCR crashed" in exc.detail


async def test_send_blocked_when_daily_quota_exceeded_and_quota_is_released():
    db = _db_with_rows([_staged_meta("f1")])
    redis = _FakeRedis()
    # Pre-exhaust the quota so this single new commit pushes over the limit.
    redis.store["docquota:commit:user-1:" + _today()] = 20

    exc = None
    try:
        await _build_file_context(
            db, _body("f1"), _request_with_redis(redis), _ctx(), _claims()
        )
    except HTTPException as e:
        exc = e

    assert exc is not None
    assert exc.status_code == 429
    # The failed attempt's increment must be given back, not permanently burned.
    assert redis.store["docquota:commit:user-1:" + _today()] == 20


def _today() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).date().isoformat()


async def test_send_skips_refiling_a_file_already_staged_this_thread():
    """meta.thread_id matches body.thread_id -> already filed in this conversation's documents at upload time, nothing left but bookkeeping."""
    meta = _staged_meta("f1", thread_id="thread-1")
    ctx = _ctx()

    _text, _images, attachments, _new = await _build_file_context(
        _db_with_rows([meta]),
        _body("f1"),
        _request_with_redis(_FakeRedis()),
        ctx,
        _claims(),
    )

    ctx.library.add.assert_not_awaited()
    ctx.file_store.download.assert_not_called()
    assert meta.rag_ingested_at is not None
    assert len(attachments) == 1


async def test_send_files_a_document_staged_under_a_different_thread():
    """meta.thread_id differs from body.thread_id (referenced from a different conversation than it was uploaded under) -> filed again
    under *this* thread's documents, rather than trying to move already-scoped rows."""
    meta = _staged_meta("f1", thread_id="other-thread")
    meta.checksum_sha256 = "abc123" * 10
    ctx = _ctx(download=b"pdf bytes")

    _text, _images, attachments, _new = await _build_file_context(
        _db_with_rows([meta]),
        _body("f1"),
        _request_with_redis(_FakeRedis()),
        ctx,
        _claims(),
    )

    ctx.library.add.assert_awaited_once()
    args, kwargs = ctx.library.add.await_args
    assert args == (b"pdf bytes", "invoice.pdf")
    assert (
        kwargs["collection"]
        == "tenants/t1/users/user-1/conversations/thread-1/documents"
    )
    assert (
        kwargs["sha256"] == meta.checksum_sha256
        and kwargs["metadata"]["thread_id"] == "thread-1"
    )
    assert meta.rag_ingested_at is not None
    assert len(attachments) == 1


async def test_send_filing_failure_releases_quota():
    meta = _staged_meta("f1", thread_id="other-thread")
    ctx = _ctx(download=b"pdf bytes")
    ctx.library.add = AsyncMock(side_effect=RuntimeError("db exploded"))
    redis = _FakeRedis()

    exc = None
    try:
        await _build_file_context(
            _db_with_rows([meta]),
            _body("f1"),
            _request_with_redis(redis),
            ctx,
            _claims(),
        )
    except RuntimeError as e:
        exc = e

    assert exc is not None
    assert redis.store["docquota:commit:user-1:" + _today()] == 0


async def test_send_multi_file_quota_blocks_both_when_insufficient_remaining():
    db = _db_with_rows([_staged_meta("f1"), _staged_meta("f2")])
    ctx = _ctx()
    redis = _FakeRedis()
    # Only 1 slot remains, but this message needs 2 — both must be blocked,
    # not one committed and one rejected.
    redis.store["docquota:commit:user-1:" + _today()] = 19

    exc = None
    try:
        await _build_file_context(
            db, _body("f1", "f2"), _request_with_redis(redis), ctx, _claims()
        )
    except HTTPException as e:
        exc = e

    assert exc is not None
    assert exc.status_code == 429
    ctx.library.add.assert_not_awaited()
    # Given back exactly what this attempt added (2), restoring to 19.
    assert redis.store["docquota:commit:user-1:" + _today()] == 19


async def test_send_skips_pre_validation_when_no_new_commits():
    """A file already committed (rag_ingested_at set) doesn't re-trigger staging validation or consume quota on every later reference."""
    meta = _staged_meta("f1", staged_at=None, rag_ingested_at="already-set")
    ctx = _ctx()

    _text, _images, attachments, _new = await _build_file_context(
        _db_with_rows([meta]),
        _body("f1"),
        _request_with_redis(_FakeRedis()),
        ctx,
        _claims(),
    )

    ctx.library.add.assert_not_awaited()
    assert len(attachments) == 1
