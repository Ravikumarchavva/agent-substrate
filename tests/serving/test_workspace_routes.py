"""Integration tests for /files upload and /workspace management routes —
tenant-scoped keys, quota enforcement, and cross-user isolation."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from substrate.capabilities.storage.workspace import WorkspaceFileStore
from substrate.serving.monolith.app import app
from substrate.serving.monolith.models import Thread, User
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.shared.auth.claims import AuthClaims

TENANT = "test-tenant"


def _claims_for(user_id: str) -> AuthClaims:
    return AuthClaims(sub=user_id, tenant_id=TENANT)


@asynccontextmanager
async def _bypass_session():
    """A session_factory() session with RLS's admin bypass GUC set — for
    direct test setup/teardown against RLS-covered tables (threads), which
    is trusted fixture code, not a real request going through
    get_tenant_scoped_db. No-op against the superuser connection tests
    normally run under; only matters once APP_DATABASE_URL points at the
    restricted role (see rls.py)."""
    session_factory = app.state.session_factory
    async with session_factory() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        yield db


@asynccontextmanager
async def _registered_user():
    """Create a real User row (file_metadata.user_id is a real FK) and
    clean it up afterwards, regardless of test outcome."""
    user_id = uuid.uuid4()
    async with _bypass_session() as db:
        db.add(User(id=user_id, identifier=f"test-{user_id}"))
        await db.commit()
    try:
        yield str(user_id)
    finally:
        async with _bypass_session() as db:
            row = await db.get(User, user_id)
            if row is not None:
                await db.delete(row)
                await db.commit()


@asynccontextmanager
async def _registered_thread(thread_id: str, *, owner: str, tenant_id: str = TENANT):
    """Create a real Thread row (file_metadata.thread_id is a real FK),
    owned by *owner* within *tenant_id* — ownership (``list_files``,
    ``delete_file``, ``_may_access_key``) is resolved through these columns,
    not the storage key."""
    tid = uuid.UUID(thread_id)
    async with _bypass_session() as db:
        db.add(
            Thread(
                id=tid,
                name="test thread",
                user_identifier=owner,
                tenant_id=tenant_id,
            )
        )
        await db.commit()
    try:
        yield thread_id
    finally:
        async with _bypass_session() as db:
            row = await db.get(Thread, tid)
            if row is not None:
                await db.delete(row)
                await db.commit()


async def _promote_file(file_id: str) -> None:
    """Simulate "the message carrying this attachment was sent" — a
    composer upload (real thread_id) only reaches the real file_store at
    that point (see routes/files.py::promote_pending_file), and these
    tests exercise /workspace/file's own serving/caching behavior against
    an already-permanent file, not the pending-upload mechanics."""
    from substrate.serving.monolith.models import FileMetadata
    from substrate.serving.monolith.routes.files import promote_pending_file

    async with _bypass_session() as db:
        meta = await db.get(FileMetadata, uuid.UUID(file_id))
        assert meta is not None
        await promote_pending_file(app.state.ctx, meta)
        await db.commit()


@pytest.mark.requires_postgres
async def test_upload_scoped_key_and_workspace_management(tmp_path) -> None:
    async with app.router.lifespan_context(app):
        # Swap in a workspace store rooted at a throwaway tmp dir so the
        # test never touches real disk under FILE_STORE_ROOT, and give
        # the tenant a small quota to exercise the 413 path.
        app.state.ctx.file_store = WorkspaceFileStore(
            root=tmp_path, user_quota_bytes=1000
        )
        app.state.ctx.workspace_user_quota_bytes = 1000
        app.state.ctx.workspace_user_delete_allowed = True

        async with _registered_user() as user_id:
            app.dependency_overrides[get_current_user] = lambda: _claims_for(user_id)
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    # Upload without a thread_id -> tenants/{tid}/users/{uid}/uploads/...
                    resp = await client.post(
                        "/files/upload",
                        files={"file": ("hello.txt", b"hello world", "text/plain")},
                    )
                    assert resp.status_code == 201
                    file_id = resp.json()["id"]
                    expected_key = f"tenants/{TENANT}/users/{user_id}/uploads/hello.txt"

                    # File lands under this tenant+user's namespace.
                    files = (await client.get("/workspace/files")).json()["files"]
                    assert any(f["path"] == expected_key for f in files)

                    # Usage reflects the upload (metered per tenant, not per
                    # user — a conversation-scoped upload carries no user
                    # segment in its key at all).
                    usage = (await client.get("/workspace/usage")).json()
                    assert usage["used_bytes"] == len(b"hello world")
                    assert usage["quota_bytes"] == 1000

                    # Quota is enforced on the next upload.
                    big_resp = await client.post(
                        "/files/upload",
                        files={
                            "file": ("big.bin", b"x" * 2000, "application/octet-stream")
                        },
                    )
                    assert big_resp.status_code == 413

                    # Download still works through the normal /files route.
                    dl = await client.get(f"/files/{file_id}/download")
                    assert dl.status_code == 200
                    assert dl.content == b"hello world"

                    # Delete via the workspace route.
                    del_resp = await client.delete(
                        "/workspace/files", params={"path": expected_key}
                    )
                    assert del_resp.status_code == 204
                    usage_after = (await client.get("/workspace/usage")).json()
                    assert usage_after["used_bytes"] == 0
            finally:
                app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.requires_postgres
async def test_upload_auto_creates_missing_user_row(tmp_path) -> None:
    """A caller whose JWT `sub` is a valid UUID but has no matching `users`
    row yet (e.g. a frontend minting per-user tokens straight from its own
    user store, like substrate-ui's Google-OAuth Prisma id) must not hit an
    IntegrityError on the FileMetadata.user_id FK — the row is
    get-or-created (see routes/files.py::_ensure_user)."""
    from substrate.serving.monolith.models import User

    async with app.router.lifespan_context(app):
        app.state.ctx.file_store = WorkspaceFileStore(
            root=tmp_path, user_quota_bytes=10_000
        )
        app.state.ctx.workspace_user_quota_bytes = 10_000

        user_id = str(uuid.uuid4())
        app.dependency_overrides[get_current_user] = lambda: AuthClaims(
            sub=user_id, email=f"{user_id}@example.com", tenant_id=TENANT
        )
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/files/upload",
                    files={"file": ("hello.txt", b"hi", "text/plain")},
                )
                assert resp.status_code == 201

            session_factory = app.state.session_factory
            async with session_factory() as db:
                row = await db.get(User, uuid.UUID(user_id))
                assert row is not None
                assert row.identifier == f"{user_id}@example.com"
        finally:
            app.dependency_overrides.pop(get_current_user, None)
            session_factory = app.state.session_factory
            async with session_factory() as db:
                row = await db.get(User, uuid.UUID(user_id))
                if row is not None:
                    await db.delete(row)
                    await db.commit()


@pytest.mark.requires_postgres
async def test_thread_scoped_upload_and_cross_user_isolation(tmp_path) -> None:
    async with app.router.lifespan_context(app):
        app.state.ctx.file_store = WorkspaceFileStore(
            root=tmp_path, user_quota_bytes=10_000
        )
        app.state.ctx.workspace_user_quota_bytes = 10_000
        app.state.ctx.workspace_user_delete_allowed = True

        async with (
            _registered_user() as user_1,
            _registered_user() as user_2,
        ):
            thread_id = str(uuid.uuid4())
            async with _registered_thread(thread_id, owner=user_1):
                app.dependency_overrides[get_current_user] = lambda: _claims_for(
                    user_1
                )
                try:
                    async with AsyncClient(
                        transport=ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        resp = await client.post(
                            "/files/upload",
                            data={"thread_id": thread_id},
                            files={
                                "file": ("notes.txt", b"secret notes", "text/plain")
                            },
                        )
                        assert resp.status_code == 201
                        key = (
                            f"tenants/{TENANT}/conversations/{thread_id}/workspace/"
                            "shared/uploads/notes.txt"
                        )
                finally:
                    app.dependency_overrides.pop(get_current_user, None)

                # A second user, who doesn't own this thread, cannot see or
                # delete the first user's file.
                app.dependency_overrides[get_current_user] = lambda: _claims_for(
                    user_2
                )
                try:
                    async with AsyncClient(
                        transport=ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        files = (await client.get("/workspace/files")).json()["files"]
                        assert all(f["path"] != key for f in files)

                        del_resp = await client.delete(
                            "/workspace/files", params={"path": key}
                        )
                        # user_2's deletion is refused either way — but once
                        # a second user is present, non-admin deletion is
                        # refused outright (403) before the per-file
                        # ownership check that would otherwise 404.
                        assert del_resp.status_code == 403
                finally:
                    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.requires_postgres
async def test_serve_file_sets_etag_and_honors_if_none_match(tmp_path) -> None:
    """serve_file previously had no cache headers at all — every inline
    chart image in a chat message re-triggered a full backend round trip
    (auth + DB + object-storage download) on every page load. An unchanged
    file must now round-trip as a cheap 304 with no body."""
    async with app.router.lifespan_context(app):
        app.state.ctx.file_store = WorkspaceFileStore(
            root=tmp_path, user_quota_bytes=10_000
        )
        app.state.ctx.workspace_user_quota_bytes = 10_000

        async with _registered_user() as user_id:
            thread_id = str(uuid.uuid4())
            async with _registered_thread(thread_id, owner=user_id):
                app.dependency_overrides[get_current_user] = lambda: _claims_for(
                    user_id
                )
                try:
                    async with AsyncClient(
                        transport=ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        upload_resp = await client.post(
                            "/files/upload",
                            data={"thread_id": thread_id},
                            files={
                                "file": ("chart.png", b"fake-png-bytes", "image/png")
                            },
                        )
                        await _promote_file(upload_resp.json()["id"])

                        first = await client.get(
                            "/workspace/file",
                            params={"thread_id": thread_id, "path": "chart.png"},
                        )
                        assert first.status_code == 200
                        etag = first.headers["etag"]
                        assert etag
                        assert first.headers["cache-control"] == "private, no-cache"

                        # Unmodified: If-None-Match round-trips as a bodyless 304.
                        cached = await client.get(
                            "/workspace/file",
                            params={"thread_id": thread_id, "path": "chart.png"},
                            headers={"if-none-match": etag},
                        )
                        assert cached.status_code == 304
                        assert cached.content == b""
                        assert cached.headers["etag"] == etag

                        # A stale/foreign ETag must still get the real file back.
                        stale = await client.get(
                            "/workspace/file",
                            params={"thread_id": thread_id, "path": "chart.png"},
                            headers={"if-none-match": '"not-the-real-etag"'},
                        )
                        assert stale.status_code == 200
                        assert stale.content == b"fake-png-bytes"
                finally:
                    app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.requires_postgres
async def test_serve_file_pinned_version_is_cached_immutable(tmp_path) -> None:
    """A `seq`-pinned historical version's bytes never change — safe to
    cache aggressively, unlike the mutable "latest" file."""
    async with app.router.lifespan_context(app):
        app.state.ctx.file_store = WorkspaceFileStore(
            root=tmp_path, user_quota_bytes=10_000
        )
        app.state.ctx.workspace_user_quota_bytes = 10_000

        async with _registered_user() as user_id:
            thread_id = str(uuid.uuid4())
            async with _registered_thread(thread_id, owner=user_id):
                app.dependency_overrides[get_current_user] = lambda: _claims_for(
                    user_id
                )
                try:
                    async with AsyncClient(
                        transport=ASGITransport(app=app), base_url="http://test"
                    ) as client:
                        upload_resp = await client.post(
                            "/files/upload",
                            data={"thread_id": thread_id},
                            files={"file": ("notes.txt", b"v1", "text/plain")},
                        )
                        await _promote_file(upload_resp.json()["id"])
                        # serve_file lazily captures a FileVersion on its first
                        # (unpinned) read (see its docstring) — a version row
                        # doesn't exist purely from the upload itself.
                        await client.get(
                            "/workspace/file",
                            params={"thread_id": thread_id, "path": "notes.txt"},
                        )

                        resp = await client.get(
                            "/workspace/file",
                            params={
                                "thread_id": thread_id,
                                "path": "notes.txt",
                                "seq": 1,
                            },
                        )
                        assert resp.status_code == 200
                        assert (
                            resp.headers["cache-control"]
                            == "public, max-age=31536000, immutable"
                        )
                finally:
                    app.dependency_overrides.pop(get_current_user, None)
