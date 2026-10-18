"""Integration tests for /admin/storage — cross-tenant usage listing,
conversation drill-down, and per-tenant quota overrides. Admin-only (403 for
a non-admin)."""

from __future__ import annotations


import pytest
from httpx import ASGITransport, AsyncClient
from substrate.stores import Store
from substrate_cloud.monolith.app import app
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.shared.auth.claims import AuthClaims


def _admin_claims() -> AuthClaims:
    return AuthClaims(sub="admin-1", tenant_id="test-tenant", role="platform_admin")


def _user_claims(user_id: str) -> AuthClaims:
    return AuthClaims(sub=user_id, tenant_id="test-tenant")


@pytest.mark.requires_postgres
async def test_storage_routes_require_admin_role(tmp_path) -> None:
    async with app.router.lifespan_context(app):
        app.state.ctx.file_store = Store.at(tmp_path, file_quota_bytes=1000).files
        app.state.ctx.file_access = None
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
        store = Store.at(tmp_path, file_quota_bytes=1000).files
        app.state.ctx.file_store = store
        app.state.ctx.file_access = None

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
                by_conv = {row["conversation_id"]: row for row in conv_resp.json()}
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
async def test_a_local_store_has_no_per_tenant_quota_to_set(tmp_path) -> None:
    """A tenant's quota is the data service's volume quota now; the local folder store has one global figure and this process keeps no
    table of overrides, so the route says so instead of pretending to persist one."""
    async with app.router.lifespan_context(app):
        app.state.ctx.file_store = Store.at(tmp_path, file_quota_bytes=1000).files
        app.state.ctx.file_access = None
        app.dependency_overrides[get_current_user] = lambda: _admin_claims()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.put(
                    "/admin/storage/tenant1/quota", json={"quota_bytes": 42}
                )
                assert resp.status_code == 501
                assert "data service" in resp.json()["detail"]
        finally:
            app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.requires_postgres
async def test_storage_routes_501_without_a_store_that_can_enumerate_tenants() -> None:
    async with app.router.lifespan_context(app):
        app.state.ctx.file_store = object()
        app.state.ctx.file_access = None
        app.dependency_overrides[get_current_user] = lambda: _admin_claims()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/admin/storage")
                assert resp.status_code == 501
        finally:
            app.dependency_overrides.pop(get_current_user, None)
