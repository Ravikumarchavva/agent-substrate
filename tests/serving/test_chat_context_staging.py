"""_build_file_context — the send-time pre-validation pass for eagerly
staged documents (local backend only): a chat send referencing a file whose
staging failed, is still in progress, or would exceed the daily commit
quota is blocked entirely (not a silent per-file degrade — see the plan's
explicit "block the whole send" decision). A file already staged *for this
exact thread* (session_id already correct in the per-user index — see
integrations/knowledge/session_ingest.py) needs no further work; one staged
under a different thread, or never staged at all, gets ingested now,
tagged with this message's real thread_id."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

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


async def test_send_skips_reingestion_for_file_already_staged_this_thread():
    """meta.thread_id matches body.thread_id -> already correctly scoped in
    the per-user index at upload time, nothing left to do but bookkeeping."""
    meta = _staged_meta("f1", thread_id="thread-1")
    db = _db_with_rows([meta])

    rag_backend = MagicMock()
    rag_backend.name = "local"
    ctx = MagicMock()
    ctx.file_store = MagicMock()
    ctx.rag_backend = rag_backend
    ctx.embedding_client = MagicMock()

    body = MagicMock()
    body.file_ids = ["f1"]
    body.thread_id = "thread-1"

    with patch(
        "substrate.integrations.knowledge.session_ingest.ingest_session_document",
        new=AsyncMock(),
    ) as mock_ingest:
        text_block, _images, attachments, _new_attachments = await _build_file_context(
            db, body, _request_with_redis(_FakeRedis()), ctx, MagicMock(sub="user-1")
        )

    mock_ingest.assert_not_awaited()
    ctx.file_store.download.assert_not_called()
    assert meta.rag_ingested_at is not None
    assert len(attachments) == 1


async def test_send_ingests_a_file_staged_under_a_different_thread():
    """meta.thread_id differs from body.thread_id (referenced from a
    different conversation than it was uploaded under) -> re-ingest, tagged
    with *this* thread's id, rather than trying to move already-scoped rows."""
    meta = _staged_meta("f1", thread_id="other-thread")
    db = _db_with_rows([meta])

    rag_backend = MagicMock()
    rag_backend.name = "local"
    ctx = MagicMock()
    ctx.file_store = MagicMock()
    ctx.file_store.download = AsyncMock(return_value=b"pdf bytes")
    ctx.rag_backend = rag_backend
    ctx.embedding_client = MagicMock()

    body = MagicMock()
    body.file_ids = ["f1"]
    body.thread_id = "thread-1"

    with patch(
        "substrate.integrations.knowledge.session_ingest.ingest_session_document",
        new=AsyncMock(),
    ) as mock_ingest:
        text_block, _images, attachments, _new_attachments = await _build_file_context(
            db, body, _request_with_redis(_FakeRedis()), ctx, MagicMock(sub="user-1")
        )

    mock_ingest.assert_awaited_once()
    assert mock_ingest.await_args.kwargs["session_id"] == "thread-1"
    assert meta.rag_ingested_at is not None
    assert len(attachments) == 1


async def test_send_non_local_backend_unaffected_by_staging_logic():
    """A non-local backend has no staging concept at all — a file with
    staged_at=None (which would 425 under local) must NOT be blocked; it
    goes straight to the existing direct-ingest path, unchanged."""
    meta = _staged_meta("f1", staged_at=None)
    db = _db_with_rows([meta])

    rag_backend = MagicMock()
    rag_backend.name = "managed"
    rag_backend.ingest = AsyncMock()
    ctx = MagicMock()
    ctx.file_store = MagicMock()
    ctx.file_store.download = AsyncMock(return_value=b"pdf bytes")
    ctx.rag_backend = rag_backend

    body = MagicMock()
    body.file_ids = ["f1"]
    body.thread_id = "thread-1"

    text_block, _images, attachments, _new_attachments = await _build_file_context(
        db, body, _request_with_redis(_FakeRedis()), ctx, MagicMock(sub="user-1")
    )

    rag_backend.ingest.assert_awaited_once()
    assert meta.rag_ingested_at is not None


async def test_send_ingest_failure_releases_quota():
    meta = _staged_meta("f1", thread_id="other-thread")
    db = _db_with_rows([meta])

    rag_backend = MagicMock()
    rag_backend.name = "local"
    ctx = MagicMock()
    ctx.file_store = MagicMock()
    ctx.file_store.download = AsyncMock(return_value=b"pdf bytes")
    ctx.rag_backend = rag_backend
    ctx.embedding_client = MagicMock()

    body = MagicMock()
    body.file_ids = ["f1"]
    body.thread_id = "thread-1"

    redis = _FakeRedis()
    exc = None
    with patch(
        "substrate.integrations.knowledge.session_ingest.ingest_session_document",
        new=AsyncMock(side_effect=RuntimeError("db exploded")),
    ):
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

    text_block, _images, attachments, _new_attachments = await _build_file_context(
        db, body, _request_with_redis(_FakeRedis()), ctx, MagicMock(sub="user-1")
    )

    rag_backend.promote.assert_not_awaited()
    rag_backend.ingest.assert_not_awaited()
    assert len(attachments) == 1
