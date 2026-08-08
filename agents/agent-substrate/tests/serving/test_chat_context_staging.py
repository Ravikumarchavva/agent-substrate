"""_build_file_context — the send-time pre-validation pass for eagerly
staged documents (local backend only): a chat send referencing a file whose
staging failed, is still in progress, or would exceed the daily commit
quota is blocked entirely (not a silent per-file degrade — see the plan's
explicit "block the whole send" decision). A file that staged successfully
gets cheaply promote()'d (re-keyed) instead of re-ingested."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi import HTTPException

from substrate.serving.monolith.routes.chat_context import _build_file_context


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


async def test_send_blocked_when_staging_still_in_progress():
    meta = _staged_meta("f1", staged_at=None)
    db = _db_with_rows([meta])

    rag_backend = MagicMock()
    rag_backend.name = "local"
    ctx = MagicMock()
    ctx.file_store = MagicMock()
    ctx.rag_backend = rag_backend

    body = MagicMock()
    body.file_ids = ["f1"]
    body.thread_id = "thread-1"

    exc = None
    try:
        await _build_file_context(
            db, body, _request_with_redis(_FakeRedis()), ctx, MagicMock(sub="user-1")
        )
    except HTTPException as e:
        exc = e

    assert exc is not None
    assert exc.status_code == 425


async def test_send_blocked_when_staging_failed():
    meta = _staged_meta("f1", staged_at=None, staging_error="OCR crashed")
    db = _db_with_rows([meta])

    rag_backend = MagicMock()
    rag_backend.name = "local"
    ctx = MagicMock()
    ctx.file_store = MagicMock()
    ctx.rag_backend = rag_backend

    body = MagicMock()
    body.file_ids = ["f1"]
    body.thread_id = "thread-1"

    exc = None
    try:
        await _build_file_context(
            db, body, _request_with_redis(_FakeRedis()), ctx, MagicMock(sub="user-1")
        )
    except HTTPException as e:
        exc = e

    assert exc is not None
    assert exc.status_code == 422
    assert "OCR crashed" in exc.detail


async def test_send_blocked_when_daily_quota_exceeded_and_quota_is_released():
    meta = _staged_meta("f1")
    db = _db_with_rows([meta])

    rag_backend = MagicMock()
    rag_backend.name = "local"
    ctx = MagicMock()
    ctx.file_store = MagicMock()
    ctx.rag_backend = rag_backend

    body = MagicMock()
    body.file_ids = ["f1"]
    body.thread_id = "thread-1"

    redis = _FakeRedis()
    # Pre-exhaust the quota so this single new commit pushes over the limit.
    redis.store["docquota:commit:user-1:" + _today()] = 20

    exc = None
    try:
        await _build_file_context(
            db, body, _request_with_redis(redis), ctx, MagicMock(sub="user-1")
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


async def test_send_promotes_staged_file_instead_of_reingesting():
    meta = _staged_meta("f1")
    db = _db_with_rows([meta])

    rag_backend = MagicMock()
    rag_backend.name = "local"
    rag_backend.promote = AsyncMock(return_value=3)
    rag_backend.ingest = AsyncMock()
    ctx = MagicMock()
    ctx.file_store = MagicMock()
    ctx.rag_backend = rag_backend

    body = MagicMock()
    body.file_ids = ["f1"]
    body.thread_id = "thread-1"

    text_block, _images, attachments = await _build_file_context(
        db, body, _request_with_redis(_FakeRedis()), ctx, MagicMock(sub="user-1")
    )

    rag_backend.promote.assert_awaited_once_with(file_id="f1", thread_id="thread-1")
    rag_backend.ingest.assert_not_awaited()
    ctx.file_store.download.assert_not_called()  # promote never needs the raw bytes
    assert meta.rag_ingested_at is not None
    assert len(attachments) == 1


async def test_send_pinecone_backend_unaffected_by_staging_logic():
    """Pinecone has no staging concept at all — a file with staged_at=None
    (which would 425 under local) must NOT be blocked; it goes straight to
    the existing direct-ingest path, unchanged."""
    meta = _staged_meta("f1", staged_at=None)
    db = _db_with_rows([meta])

    rag_backend = MagicMock()
    rag_backend.name = "pinecone"
    rag_backend.ingest = AsyncMock()
    ctx = MagicMock()
    ctx.file_store = MagicMock()
    ctx.file_store.download = AsyncMock(return_value=b"pdf bytes")
    ctx.rag_backend = rag_backend

    body = MagicMock()
    body.file_ids = ["f1"]
    body.thread_id = "thread-1"

    text_block, _images, attachments = await _build_file_context(
        db, body, _request_with_redis(_FakeRedis()), ctx, MagicMock(sub="user-1")
    )

    rag_backend.ingest.assert_awaited_once()
    assert meta.rag_ingested_at is not None


async def test_send_promote_failure_releases_quota():
    meta = _staged_meta("f1")
    db = _db_with_rows([meta])

    rag_backend = MagicMock()
    rag_backend.name = "local"
    rag_backend.promote = AsyncMock(side_effect=RuntimeError("db exploded"))
    ctx = MagicMock()
    ctx.file_store = MagicMock()
    ctx.rag_backend = rag_backend

    body = MagicMock()
    body.file_ids = ["f1"]
    body.thread_id = "thread-1"

    redis = _FakeRedis()
    exc = None
    try:
        await _build_file_context(
            db, body, _request_with_redis(redis), ctx, MagicMock(sub="user-1")
        )
    except RuntimeError as e:
        exc = e

    assert exc is not None
    assert redis.store["docquota:commit:user-1:" + _today()] == 0


async def test_send_multi_file_quota_blocks_both_when_insufficient_remaining():
    meta1 = _staged_meta("f1")
    meta2 = _staged_meta("f2")
    db = _db_with_rows([meta1, meta2])

    rag_backend = MagicMock()
    rag_backend.name = "local"
    rag_backend.promote = AsyncMock()
    ctx = MagicMock()
    ctx.file_store = MagicMock()
    ctx.rag_backend = rag_backend

    body = MagicMock()
    body.file_ids = ["f1", "f2"]
    body.thread_id = "thread-1"

    redis = _FakeRedis()
    # Only 1 slot remains, but this message needs 2 — both must be blocked,
    # not one committed and one rejected.
    redis.store["docquota:commit:user-1:" + _today()] = 19

    exc = None
    try:
        await _build_file_context(
            db, body, _request_with_redis(redis), ctx, MagicMock(sub="user-1")
        )
    except HTTPException as e:
        exc = e

    assert exc is not None
    assert exc.status_code == 429
    rag_backend.promote.assert_not_awaited()
    # Given back exactly what this attempt added (2), restoring to 19.
    assert redis.store["docquota:commit:user-1:" + _today()] == 19


async def test_send_skips_pre_validation_when_no_new_commits():
    """A file already committed (rag_ingested_at set) doesn't re-trigger
    staging validation or consume quota on every later reference."""
    meta = _staged_meta("f1", staged_at=None, rag_ingested_at="already-set")
    db = _db_with_rows([meta])

    rag_backend = MagicMock()
    rag_backend.name = "local"
    rag_backend.promote = AsyncMock()
    rag_backend.ingest = AsyncMock()
    ctx = MagicMock()
    ctx.file_store = MagicMock()
    ctx.rag_backend = rag_backend

    body = MagicMock()
    body.file_ids = ["f1"]
    body.thread_id = "thread-1"

    text_block, _images, attachments = await _build_file_context(
        db, body, _request_with_redis(_FakeRedis()), ctx, MagicMock(sub="user-1")
    )

    rag_backend.promote.assert_not_awaited()
    rag_backend.ingest.assert_not_awaited()
    assert len(attachments) == 1
