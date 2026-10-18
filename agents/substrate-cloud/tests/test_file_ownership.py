"""File-ownership enforcement (IDOR regression tests).

``GET/DELETE /files/{file_id}/*`` used to have no ownership check at all — any authenticated user could read/delete any file by id,
which matters once citations start putting file ids in the chat stream (routes/files.py, routes/chat_context.py). These tests pin the
fix: ``_may_access_key`` (objects resolved structurally off the key's own tenant/user/conversation segments), ``GET /files/object``
on top of it, and the file-id routes, where a file's record lives in its owner's own folder so another user's id is simply not found.
"""

from __future__ import annotations

from tests._stores import fs_files

import uuid
from datetime import datetime, timezone

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from substrate_cloud.monolith.database import get_db
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.file_records import FileIndex, FileRecord
from substrate_cloud.monolith.models import Thread
from substrate_cloud.monolith.routes.files import (
    _get_meta,
    _may_access_key,
    router,
)
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.shared.auth.claims import AuthClaims

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
OWNER = AuthClaims(sub="owner-user", tenant_id=TENANT_A)
STRANGER = AuthClaims(sub="stranger-user", tenant_id=TENANT_A)
CROSS_TENANT_STRANGER = AuthClaims(sub="owner-user", tenant_id=TENANT_B)
ADMIN = AuthClaims(sub="admin-user", role="platform_admin", tenant_id=TENANT_A)


def _ctx(file_store) -> ServerDependencies:
    return ServerDependencies(
        model_client=None,
        history=None,
        tools=None,
        bridge_registry=None,
        tools_requiring_approval=[],
        system_instructions="",
        tool_timeout=60.0,
        file_store=file_store,
    )


async def _seed_file(file_store, *, owner: AuthClaims) -> FileRecord:
    """An uploaded file as the upload route leaves it: the bytes, and the record beside them in the owner's folder."""
    key = (
        f"tenants/{owner.tenant_id}/users/{owner.sub}/uploads/secret-{uuid.uuid4()}.pdf"
    )
    await file_store.upload(key, b"pdf bytes", content_type="application/pdf")
    record = FileRecord(
        id=uuid.uuid4(),
        tenant_id=owner.tenant_id,
        owner=owner.sub,
        object_key=key,
        original_name="secret.pdf",
        content_type="application/pdf",
        size_bytes=9,
        uploaded_at=datetime.now(timezone.utc),
    )
    await FileIndex(file_store, owner.tenant_id, owner.sub).save(record)
    return record


# ── file ids: the record is in the owner's folder, so a stranger's lookup finds nothing ──


async def test_owner_reads_own_file_record():
    store = fs_files()
    record = await _seed_file(store, owner=OWNER)
    found = await _get_meta(record.id, _ctx(store), OWNER)
    assert found.id == record.id and found.object_key == record.object_key


async def test_stranger_gets_404():
    """The regression test: a stranger must not be able to download another user's file by id."""
    store = fs_files()
    record = await _seed_file(store, owner=OWNER)
    with pytest.raises(HTTPException) as exc_info:
        await _get_meta(record.id, _ctx(store), STRANGER)
    assert exc_info.value.status_code == 404


async def test_cross_tenant_same_sub_gets_404():
    """Matching claims.sub isn't enough across a tenant boundary."""
    store = fs_files()
    record = await _seed_file(store, owner=OWNER)
    with pytest.raises(HTTPException) as exc_info:
        await _get_meta(record.id, _ctx(store), CROSS_TENANT_STRANGER)
    assert exc_info.value.status_code == 404


async def test_missing_file_also_404s():
    """Missing-vs-forbidden must be indistinguishable — same 404 either way, so the endpoint can't be used to probe which ids exist."""
    with pytest.raises(HTTPException) as exc_info:
        await _get_meta(uuid.uuid4(), _ctx(fs_files()), STRANGER)
    assert exc_info.value.status_code == 404


async def test_a_record_whose_file_is_gone_is_not_live():
    store = fs_files()
    record = await _seed_file(store, owner=OWNER)
    await store.delete(
        record.object_key
    )  # deleted by path, as the Storage browser does
    with pytest.raises(HTTPException) as exc_info:
        await _get_meta(record.id, _ctx(store), OWNER)
    assert exc_info.value.status_code == 404


async def test_a_started_but_unfinished_upload_is_not_live():
    store = fs_files()
    record = await _seed_file(store, owner=OWNER)
    record.uploaded_at = None
    await FileIndex(store, OWNER.tenant_id, OWNER.sub).save(record)
    with pytest.raises(HTTPException):
        await _get_meta(record.id, _ctx(store), OWNER)


async def test_the_thread_listing_finds_only_that_threads_live_files():
    store = fs_files()
    thread_a, thread_b = uuid.uuid4(), uuid.uuid4()
    index = FileIndex(store, OWNER.tenant_id, OWNER.sub)
    a = await _seed_file(store, owner=OWNER)
    b = await _seed_file(store, owner=OWNER)
    a.thread_id, b.thread_id = thread_a, thread_b
    await index.save(a)
    await index.save(b)
    assert [r.id for r in await index.for_thread(thread_a)] == [a.id]
    assert [r.id for r in await index.for_thread(thread_b, extra=[a.id])] == [
        b.id,
        a.id,
    ] or {r.id for r in await index.for_thread(thread_b, extra=[a.id])} == {a.id, b.id}
    await store.delete(a.object_key)
    assert await index.for_thread(thread_a) == []


# ── _may_access_key (pure except the conversation-ownership branch — objects
# with no FileMetadata row, resolved structurally off the key itself) ───────


async def test_own_direct_upload_prefix_allowed(db_session=None):
    key = f"tenants/{TENANT_A}/users/{OWNER.sub}/uploads/a.png"
    assert await _may_access_key(key, OWNER, db=None) is True  # type: ignore[arg-type]


async def test_another_users_direct_upload_prefix_denied():
    key = f"tenants/{TENANT_A}/users/{OWNER.sub}/uploads/a.png"
    assert await _may_access_key(key, STRANGER, db=None) is False  # type: ignore[arg-type]


async def test_knowledge_base_key_allowed_for_any_same_tenant_caller():
    """Knowledge-base documents are project-shared within a tenant — the
    replacement for the old, cross-tenant ``_OPEN_NAMESPACES = ("kb/",)``
    rule."""
    key = f"tenants/{TENANT_A}/knowledge/kb1/documents/doc1/original/a.pdf"
    assert await _may_access_key(key, STRANGER, db=None) is True  # type: ignore[arg-type]


async def test_knowledge_base_key_denied_across_tenants():
    key = f"tenants/{TENANT_A}/knowledge/kb1/documents/doc1/original/a.pdf"
    assert await _may_access_key(key, CROSS_TENANT_STRANGER, db=None) is False  # type: ignore[arg-type]


async def test_key_outside_any_recognized_namespace_denied():
    assert await _may_access_key("shared/reference.bin", STRANGER, db=None) is False  # type: ignore[arg-type]


async def test_conversation_key_allowed_for_the_owning_thread(database_url: str):
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    thread_id = uuid.uuid4()
    async with factory() as session:
        session.add(Thread(id=thread_id, user_identifier=OWNER.sub, tenant_id=TENANT_A))
        await session.commit()
    try:
        async with factory() as session:
            key = f"tenants/{TENANT_A}/users/{OWNER.sub}/conversations/{thread_id}/workspace/shared/a.png"
            assert await _may_access_key(key, OWNER, session) is True
            assert await _may_access_key(key, STRANGER, session) is False
    finally:
        async with factory() as session:
            row = await session.get(Thread, thread_id)
            if row is not None:
                await session.delete(row)
                await session.commit()
        await engine.dispose()


# ── GET /files/object — falls back to _may_access_key when there's no
# record at all (RAG images and other capability-written
# artifacts are uploaded straight to the store with none) ──────────────────


@pytest.fixture
def object_route_app():
    app = FastAPI()
    app.include_router(router)
    file_store = fs_files()
    app.dependency_overrides[get_ctx] = lambda: ServerDependencies(
        model_client=None,
        history=None,
        tools=None,
        bridge_registry=None,
        tools_requiring_approval=[],
        system_instructions="",
        tool_timeout=60.0,
        file_store=file_store,
    )
    yield app, file_store
    app.dependency_overrides.clear()


async def test_object_route_serves_the_owners_key(object_route_app, database_url: str):
    app, file_store = object_route_app
    key = f"tenants/{TENANT_A}/users/{OWNER.sub}/uploads/p1.png"
    await file_store.upload(key, b"PNGBYTES")
    app.dependency_overrides[get_current_user] = lambda: OWNER
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    app.dependency_overrides[get_db] = lambda: factory()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            resp = await http.get("/files/object", params={"key": key})
        assert resp.status_code == 200
        assert resp.content == b"PNGBYTES"
    finally:
        await engine.dispose()


async def test_object_route_denies_a_key_under_another_users_prefix(
    object_route_app, database_url: str
):
    app, file_store = object_route_app
    key = f"tenants/{TENANT_A}/users/{OWNER.sub}/uploads/p1.png"
    await file_store.upload(key, b"PNGBYTES")
    app.dependency_overrides[get_current_user] = lambda: STRANGER
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    app.dependency_overrides[get_db] = lambda: factory()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            resp = await http.get("/files/object", params={"key": key})
        # 404, not 403: this must not become an existence oracle for keys that
        # leak into logs/URLs (same rationale as _get_meta above).
        assert resp.status_code == 404
    finally:
        await engine.dispose()


async def test_object_route_serves_a_kb_key_to_any_same_tenant_caller(
    object_route_app, database_url: str
):
    app, file_store = object_route_app
    key = f"tenants/{TENANT_A}/knowledge/kb1/documents/doc1/original/a.pdf"
    await file_store.upload(key, b"CHARTBYTES")
    app.dependency_overrides[get_current_user] = lambda: STRANGER
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    app.dependency_overrides[get_db] = lambda: factory()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            resp = await http.get("/files/object", params={"key": key})
        assert resp.status_code == 200
        assert resp.content == b"CHARTBYTES"
    finally:
        await engine.dispose()


async def test_object_route_denies_a_kb_key_across_tenants(
    object_route_app, database_url: str
):
    app, file_store = object_route_app
    key = f"tenants/{TENANT_A}/knowledge/kb1/documents/doc1/original/a.pdf"
    await file_store.upload(key, b"CHARTBYTES")
    app.dependency_overrides[get_current_user] = lambda: CROSS_TENANT_STRANGER
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    app.dependency_overrides[get_db] = lambda: factory()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            resp = await http.get("/files/object", params={"key": key})
        assert resp.status_code == 404
    finally:
        await engine.dispose()


async def test_object_route_denies_a_key_outside_any_recognized_namespace(
    object_route_app, database_url: str
):
    app, file_store = object_route_app
    await file_store.upload("shared/reference.bin", b"SECRETBYTES")
    app.dependency_overrides[get_current_user] = lambda: STRANGER
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    app.dependency_overrides[get_db] = lambda: factory()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            resp = await http.get(
                "/files/object", params={"key": "shared/reference.bin"}
            )
        assert resp.status_code == 404
    finally:
        await engine.dispose()


async def test_object_route_404s_on_a_missing_key_not_a_500(
    object_route_app, database_url: str
):
    app, _file_store = object_route_app
    app.dependency_overrides[get_current_user] = lambda: OWNER
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    app.dependency_overrides[get_db] = lambda: factory()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            resp = await http.get(
                "/files/object",
                params={
                    "key": f"tenants/{TENANT_A}/users/{OWNER.sub}/uploads/missing.png"
                },
            )
        assert resp.status_code == 404
    finally:
        await engine.dispose()


# ── Route-level: proves the Depends chain is actually wired ─────────────────
#
# httpx.AsyncClient + ASGITransport, not the sync starlette TestClient — the latter runs the app in a separate thread with its own event
# loop, and an asyncpg connection created in this test's loop then used from that other loop raises "attached to a different loop".
# Staying on one loop avoids it.


@pytest.fixture
def app_with_overrides():
    app = FastAPI()
    app.include_router(router)
    file_store = fs_files()
    app.dependency_overrides[get_ctx] = lambda: _ctx(file_store)
    yield app, file_store
    app.dependency_overrides.clear()


async def test_route_download_denies_stranger_with_404(app_with_overrides):
    app, file_store = app_with_overrides
    record = await _seed_file(file_store, owner=OWNER)
    app.dependency_overrides[get_current_user] = lambda: STRANGER
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        resp = await http.get(f"/files/{record.id}/download")
    assert resp.status_code == 404


async def test_route_download_allows_owner(app_with_overrides):
    app, file_store = app_with_overrides
    record = await _seed_file(file_store, owner=OWNER)
    app.dependency_overrides[get_current_user] = lambda: OWNER
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http:
        resp = await http.get(f"/files/{record.id}/download")
    assert resp.status_code == 200
    assert resp.content == b"pdf bytes"


async def test_route_url_and_delete_are_the_owners_only(
    app_with_overrides, database_url: str
):
    app, file_store = app_with_overrides
    record = await _seed_file(file_store, owner=OWNER)
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    app.dependency_overrides[get_db] = lambda: factory()
    app.dependency_overrides[get_current_user] = lambda: STRANGER
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            assert (await http.get(f"/files/{record.id}/url")).status_code == 404
            assert (await http.get(f"/files/{record.id}/status")).status_code == 404
            assert (await http.delete(f"/files/{record.id}")).status_code == 404
        assert await file_store.exists(record.object_key)
        app.dependency_overrides[get_current_user] = lambda: OWNER
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as http:
            assert (await http.delete(f"/files/{record.id}")).status_code == 204
        assert not await file_store.exists(record.object_key)
    finally:
        await engine.dispose()
