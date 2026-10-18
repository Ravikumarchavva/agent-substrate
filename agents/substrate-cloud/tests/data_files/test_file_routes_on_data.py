"""The file, workspace and storage routes with ``DATA_SERVICE_URL`` set: bytes and quotas live in the data service, a user's files are
invisible to everyone else, and browsers upload straight to signed URLs."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from substrate.integrations.data import DataFilesConnection
from substrate_cloud.file_access import FileAccess
from substrate_cloud.monolith.database import get_db
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.routes.files import router as files_router
from substrate_cloud.monolith.routes.workspace import router as workspace_router
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.shared.auth.claims import AuthClaims

from .conftest import OTHER_TENANT, TENANT, DataService

pytestmark = pytest.mark.integration


def claims(sub: str, tenant: str = TENANT) -> AuthClaims:
    return AuthClaims(sub=sub, tenant_id=tenant)


@pytest.fixture
async def platform(
    data_service: DataService, database_url: str
) -> AsyncIterator[tuple[FastAPI, ServerDependencies]]:
    from substrate_cloud.monolith.models import Base

    connection = DataFilesConnection(
        data_service.url, tokens=data_service.minter().mint_files
    )
    access = FileAccess(connection=connection)
    ctx = ServerDependencies(
        model_client=None,
        history=None,
        tools=None,
        bridge_registry=None,
        tools_requiring_approval=[],
        system_instructions="",
        tool_timeout=60.0,
        file_store=access.shared,
        file_access=access,
    )
    app = FastAPI()
    app.include_router(files_router)
    app.include_router(workspace_router)
    app.dependency_overrides[get_ctx] = lambda: ctx
    engine = create_async_engine(database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )

    async def session() -> AsyncIterator[AsyncSession]:
        # Closed after each request: an open transaction would hold locks the app's own startup (ALTER TABLE) waits on.
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = session
    yield app, ctx
    await engine.dispose()
    await access.aclose()


def as_user(app: FastAPI, who: AuthClaims) -> httpx.AsyncClient:
    app.dependency_overrides[get_current_user] = lambda: who
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


async def test_an_upload_lands_in_the_data_service_and_only_its_owner_can_reach_it(
    platform, data_service
) -> None:
    app, _ctx = platform
    sub = f"user-{uuid.uuid4().hex[:8]}"
    async with as_user(app, claims(sub)) as owner:
        up = await owner.post(
            "/files/upload", files={"file": ("plan.txt", b"the plan", "text/plain")}
        )
        assert up.status_code == 201
        file_id = up.json()["id"]
        assert (await owner.get(f"/files/{file_id}/download")).content == b"the plan"
        url = (await owner.get(f"/files/{file_id}/url")).json()["url"]
    # The bytes are in the data service, under the user's own folder (read as an administrator):
    with data_service.admin() as admin:
        assert (
            admin.files.read(
                "tenant_acme.workspace.files", f"users/{sub}/uploads/plan.txt"
            )
            == b"the plan"
        )
    # ... and the browser can fetch them straight from object storage on the signed URL: no bearer token, not this server.
    assert url.startswith("http") and httpx.get(url).content == b"the plan"

    async with as_user(app, claims("someone-else")) as stranger:
        assert (await stranger.get(f"/files/{file_id}/download")).status_code == 404
        assert (await stranger.delete(f"/files/{file_id}")).status_code == 404
    async with as_user(app, claims(sub, OTHER_TENANT)) as other_tenant:
        assert (await other_tenant.get(f"/files/{file_id}/download")).status_code == 404
        assert (await other_tenant.get("/workspace/usage")).json()["used_bytes"] == 0
    async with as_user(app, claims(sub)) as owner:
        assert (await owner.get(f"/files/{file_id}/download")).status_code == 200


async def test_a_browser_uploads_straight_to_the_signed_url_and_completes(
    platform, data_service
) -> None:
    app, _ctx = platform
    sub = f"user-{uuid.uuid4().hex[:8]}"
    payload = b"straight to storage " * 1000
    async with as_user(app, claims(sub)) as browser:
        started = await browser.post(
            "/files/uploads",
            json={
                "name": "big.bin",
                "size": len(payload),
                "content_type": "application/octet-stream",
                "sha256": hashlib.sha256(payload).hexdigest(),
            },
        )
        assert started.status_code == 201
        ticket = started.json()
        assert ticket["method"] == "PUT" and ticket["url"]
        # Not downloadable (or even known) before the bytes arrive and the upload is completed.
        assert (await browser.get(f"/files/{ticket['id']}/download")).status_code == 404
        sent = httpx.put(ticket["url"], content=payload, headers=ticket["headers"])
        assert sent.status_code in (200, 201), sent.text
        done = await browser.post(
            f"/files/uploads/{ticket['id']}/complete", json={"parts": []}
        )
        assert done.status_code == 200 and done.json()["size"] == len(payload)
        assert (await browser.get(f"/files/{ticket['id']}/download")).content == payload
        # Someone else cannot complete (or find) it:
    async with as_user(app, claims("someone-else")) as stranger:
        assert (
            await stranger.post(
                f"/files/uploads/{ticket['id']}/complete", json={"parts": []}
            )
        ).status_code == 404


async def test_a_declared_size_is_the_enforced_one(platform) -> None:
    app, _ctx = platform
    async with as_user(app, claims(f"user-{uuid.uuid4().hex[:8]}")) as browser:
        ticket = (
            await browser.post("/files/uploads", json={"name": "x.bin", "size": 10})
        ).json()
        refused = httpx.put(
            ticket["url"], content=b"far more than ten bytes", headers=ticket["headers"]
        )
        assert refused.status_code >= 400
        assert (
            await browser.post(
                f"/files/uploads/{ticket['id']}/complete", json={"parts": []}
            )
        ).status_code == 409


async def test_the_volume_quota_is_the_workspace_quota(platform, data_service) -> None:
    app, _ctx = platform
    tenant = "quota" + uuid.uuid4().hex[:6]
    with data_service.admin() as admin:
        admin.tenants.ensure(tenant, tenant)
        admin.files.set_quota(f"tenant_{tenant}.workspace.files", 2000)
    sub = "user-q"
    async with as_user(app, claims(sub, tenant)) as client:
        usage = (await client.get("/workspace/usage")).json()
        assert usage["quota_bytes"] == 2000 and usage["used_bytes"] == 0
        ok = await client.post(
            "/files/upload",
            files={"file": ("a.bin", b"x" * 500, "application/octet-stream")},
        )
        assert ok.status_code == 201
        over = await client.post(
            "/files/upload",
            files={"file": ("b.bin", b"x" * 5000, "application/octet-stream")},
        )
        assert over.status_code == 413
        assert (await client.get("/workspace/usage")).json()["used_bytes"] > 500


async def test_the_workspace_listing_shows_only_the_callers_files(platform) -> None:
    app, _ctx = platform
    mine, theirs = f"user-{uuid.uuid4().hex[:8]}", f"user-{uuid.uuid4().hex[:8]}"
    async with as_user(app, claims(mine)) as client:
        await client.post(
            "/files/upload", files={"file": ("mine.txt", b"1", "text/plain")}
        )
    async with as_user(app, claims(theirs)) as client:
        await client.post(
            "/files/upload", files={"file": ("theirs.txt", b"2", "text/plain")}
        )
    async with as_user(app, claims(mine)) as client:
        names = {
            f["name"] for f in (await client.get("/workspace/files")).json()["files"]
        }
    assert "mine.txt" in names and "theirs.txt" not in names


async def test_the_admin_storage_page_reads_tenants_usage_and_sets_volume_quotas(
    platform, data_service
) -> None:
    from substrate_data_client import AsyncDataClient
    from substrate_cloud.storage_admin import DataStorageAdmin

    app, ctx = platform
    tenant = "adm" + uuid.uuid4().hex[:6]
    with data_service.admin() as admin:
        admin.tenants.ensure(tenant, tenant)
    async with as_user(app, claims("user-a", tenant)) as client:
        await client.post(
            "/files/upload", files={"file": ("a.txt", b"12345", "text/plain")}
        )
    client = AsyncDataClient(
        data_service.url,
        token=lambda: data_service.identity.token("alice", ("data-admins",)),
    )
    try:
        storage = DataStorageAdmin(client, ctx.file_access.connection.volume_of)
        by_id = {row["tenant_id"]: row for row in await storage.tenants()}
        assert by_id[tenant]["used_bytes"] > 5 and by_id[tenant]["quota_bytes"] is None
        assert (await storage.set_quota(tenant, 123456)) == {
            "tenant_id": tenant,
            "quota_bytes": 123456,
        }
        assert (
            await storage.tenants()
            and {r["tenant_id"]: r for r in await storage.tenants()}[tenant][
                "quota_bytes"
            ]
        ) == 123456
        assert await storage.set_quota(tenant, None) == {
            "tenant_id": tenant,
            "quota_bytes": None,
        }
        assert await storage.conversations(tenant) == []
    finally:
        await client.aclose()
