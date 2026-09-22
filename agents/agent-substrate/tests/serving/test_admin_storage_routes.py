"""Integration tests for /admin/storage — cross-tenant usage listing,
conversation drill-down, and per-tenant quota overrides. Admin-only (403 for
a non-admin)."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from substrate.agents.storage.local_object_store import WorkspaceFileStore
from substrate.serving.monolith.app import app
from substrate.serving.monolith.models import WorkspaceQuota
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.shared.auth.claims import AuthClaims


def _admin_claims() -> AuthClaims:
    return AuthClaims(sub="admin-1", tenant_id="test-tenant", role="platform_admin")


def _user_claims(user_id: str) -> AuthClaims:
    return AuthClaims(sub=user_id, tenant_id="test-tenant")


@asynccontextmanager
async def _bypass_session():
    """A session_factory() session with RLS's admin bypass GUC set — for
    direct test setup/teardown against RLS-covered tables
    (workspace_quotas), trusted fixture code rather than a real request
    going through get_tenant_scoped_db/get_service_scoped_db. No-op
    against the superuser connection tests normally run under; only
    matters once APP_DATABASE_URL points at the restricted role (rls.py)."""
    session_factory = app.state.session_factory
    async with session_factory() as db:
        await db.execute(text("SELECT set_config('app.bypass_rls', 'on', false)"))
        yield db


@asynccontextmanager
async def _no_quota_row(tenant_id: str):
    """Ensure no stray workspace_quotas row for *tenant_id* survives the
    test, regardless of outcome — set_storage_quota writes real DB rows."""
    try:
        yield
    finally:
        async with _bypass_session() as db:
            row = await db.get(WorkspaceQuota, tenant_id)
            if row is not None:
                await db.delete(row)
                await db.commit()


@pytest.mark.requires_postgres
async def test_storage_routes_require_admin_role(tmp_path) -> None:
    async with app.router.lifespan_context(app):
        app.state.ctx.file_store = WorkspaceFileStore(
            root=tmp_path, user_quota_bytes=1000
        )
        app.dependency_overrides[get_current_user] = lambda: _user_claims("u1")
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/admin/storage")
                assert resp.status_code == 403
        finally:
            app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.requires_postgres
async def test_list_storage_tenants_and_conversations(tmp_path) -> None:
    async with app.router.lifespan_context(app):
        store = WorkspaceFileStore(root=tmp_path, user_quota_bytes=1000)
        await store.connect()
        app.state.ctx.file_store = store

        # c1 and c2 belong to different users under the same tenant —
        # conversations nest under their owning user, not the tenant
        # directly (agents/workspace/layout.py), so the drill-down must
        # walk every user directory, not a single tenant-level
        # "conversations/" that no longer exists.
        await store.upload(
            "tenants/tenant1/users/u1/conversations/c1/workspace/shared/a.txt",
            b"aaa",
        )
        await store.upload(
            "tenants/tenant1/users/u2/conversations/c2/workspace/shared/b.txt",
            b"bb",
        )
        await store.upload("tenants/tenant2/users/u2/uploads/c.txt", b"c")

        app.dependency_overrides[get_current_user] = lambda: _admin_claims()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/admin/storage")
                assert resp.status_code == 200
                by_id = {row["tenant_id"]: row for row in resp.json()}
                assert by_id["tenant1"]["used_bytes"] == 5
                assert by_id["tenant1"]["conversation_count"] == 2
                assert by_id["tenant1"]["quota_bytes"] == 1000
                assert by_id["tenant2"]["used_bytes"] == 1
                assert by_id["tenant2"]["conversation_count"] == 0

                conv_resp = await client.get("/admin/storage/tenant1/conversations")
                assert conv_resp.status_code == 200
                by_conv = {
                    row["conversation_id"]: row for row in conv_resp.json()
                }
                assert by_conv["c1"] == {
                    "conversation_id": "c1",
                    "size_bytes": 3,
                    "file_count": 1,
                }
                assert by_conv["c2"] == {
                    "conversation_id": "c2",
                    "size_bytes": 2,
                    "file_count": 1,
                }
        finally:
            app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.requires_postgres
async def test_set_quota_persists_and_takes_effect_immediately(tmp_path) -> None:
    async with app.router.lifespan_context(app):
        store = WorkspaceFileStore(root=tmp_path, user_quota_bytes=1000)
        await store.connect()
        app.state.ctx.file_store = store

        app.dependency_overrides[get_current_user] = lambda: _admin_claims()
        async with _no_quota_row("tenant1"):
            try:
                async with AsyncClient(
                    transport=ASGITransport(app=app), base_url="http://test"
                ) as client:
                    resp = await client.put(
                        "/admin/storage/tenant1/quota", json={"quota_bytes": 42}
                    )
                    assert resp.status_code == 200
                    assert resp.json() == {"tenant_id": "tenant1", "quota_bytes": 42}

                    # Live on the store — no restart needed.
                    assert store.effective_quota("tenant1") == 42

                    # Persisted — a fresh store loaded the same way admin
                    # startup does would see it too.
                    async with _bypass_session() as db:
                        row = await db.get(WorkspaceQuota, "tenant1")
                        assert row is not None
                        assert row.quota_bytes == 42

                    # Reset to default.
                    reset_resp = await client.put(
                        "/admin/storage/tenant1/quota", json={"quota_bytes": None}
                    )
                    assert reset_resp.status_code == 200
                    assert reset_resp.json()["quota_bytes"] == 1000
                    assert store.effective_quota("tenant1") == 1000

                    async with _bypass_session() as db:
                        assert await db.get(WorkspaceQuota, "tenant1") is None
            finally:
                app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.requires_postgres
async def test_storage_routes_501_for_non_workspace_backend() -> None:
    """S3FileStore/InMemoryFileStore don't support per-user quota overrides
    or list_all_users — the admin storage API is explicitly local-only."""
    from substrate.agents.storage.memory import InMemoryFileStore

    async with app.router.lifespan_context(app):
        app.state.ctx.file_store = InMemoryFileStore()
        app.dependency_overrides[get_current_user] = lambda: _admin_claims()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/admin/storage")
                assert resp.status_code == 501
        finally:
            app.dependency_overrides.pop(get_current_user, None)
