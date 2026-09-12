"""File-ownership enforcement (IDOR regression tests).

``GET/DELETE /files/{file_id}/*`` used to have no ownership check at all —
any authenticated user could read/delete any file by id, which matters once
citations start putting file ids in the chat stream (routes/files.py,
routes/chat_context.py). These tests pin the fix at three levels: the pure
``_may_access`` predicate (metadata-row objects), ``_may_access_key`` (objects
with no metadata row — RAG images and other capability-written artifacts,
resolved structurally off the key's own tenant/user/conversation segments),
and ``_get_meta`` against a real DB row (mirrors
tests/serving/test_thread_ownership.py's pattern for threads), plus one
route-level pass proving the ``Depends`` chain is actually wired.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from substrate.agents.storage.memory import InMemoryFileStore
from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.models import FileMetadata, Thread, User
from substrate.serving.monolith.routes.files import (
    _get_meta,
    _may_access,
    _may_access_key,
    router,
)
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.shared.auth.claims import AuthClaims

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
OWNER = AuthClaims(sub="owner-user", tenant_id=TENANT_A)
STRANGER = AuthClaims(sub="stranger-user", tenant_id=TENANT_A)
CROSS_TENANT_STRANGER = AuthClaims(sub="owner-user", tenant_id=TENANT_B)
ADMIN = AuthClaims(sub="admin-user", role="platform_admin", tenant_id=TENANT_A)


def _meta(
    *,
    object_key: str,
    user_id: uuid.UUID | None = None,
    org_id: str | None = TENANT_A,
) -> FileMetadata:
    return FileMetadata(
        id=uuid.uuid4(),
        object_key=object_key,
        original_name="secret.pdf",
        content_type="application/pdf",
        size_bytes=10,
        user_id=user_id,
        org_id=org_id,
    )


# ── _may_access (pure — no DB, metadata-row objects) ─────────────────────────


def test_owner_may_access_via_user_id_column():
    owner_uuid = uuid.uuid4()
    claims = AuthClaims(sub=str(owner_uuid), tenant_id=TENANT_A)
    meta = _meta(object_key="anything/secret.pdf", user_id=owner_uuid)
    assert _may_access(meta, claims) is True


def test_stranger_denied():
    owner_uuid = uuid.uuid4()
    meta = _meta(object_key="anything/secret.pdf", user_id=owner_uuid)
    assert _may_access(meta, STRANGER) is False


def test_admin_bypasses_ownership():
    meta = _meta(object_key="anything/secret.pdf", user_id=uuid.uuid4())
    assert _may_access(meta, ADMIN) is True


def test_cross_tenant_same_sub_denied():
    """Matching claims.sub isn't enough across a tenant boundary."""
    owner_uuid = uuid.uuid4()
    meta = _meta(object_key="anything/secret.pdf", user_id=owner_uuid, org_id=TENANT_A)
    claims = AuthClaims(sub=str(owner_uuid), tenant_id=TENANT_B)
    assert _may_access(meta, claims) is False


def test_no_owner_signal_at_all_denied():
    meta = _meta(object_key="orphaned/secret.pdf", user_id=None)
    assert _may_access(meta, STRANGER) is False


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
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    thread_id = uuid.uuid4()
    async with factory() as session:
        session.add(
            Thread(id=thread_id, user_identifier=OWNER.sub, tenant_id=TENANT_A)
        )
        await session.commit()
    try:
        async with factory() as session:
            key = f"tenants/{TENANT_A}/conversations/{thread_id}/workspace/shared/a.png"
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
# FileMetadata row at all (RAG images and other capability-written
# artifacts are uploaded straight to the store with none) ──────────────────


@pytest.fixture
def object_route_app():
    app = FastAPI()
    app.include_router(router)
    file_store = InMemoryFileStore()
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


async def test_object_route_serves_the_owners_key(
    object_route_app, database_url: str
):
    app, file_store = object_route_app
    key = f"tenants/{TENANT_A}/users/{OWNER.sub}/uploads/p1.png"
    await file_store.upload(key, b"PNGBYTES")
    app.dependency_overrides[get_current_user] = lambda: OWNER
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    app.dependency_overrides[get_db] = lambda: factory()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
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
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    app.dependency_overrides[get_db] = lambda: factory()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
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
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    app.dependency_overrides[get_db] = lambda: factory()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
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
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    app.dependency_overrides[get_db] = lambda: factory()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
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
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    app.dependency_overrides[get_db] = lambda: factory()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            resp = await http.get("/files/object", params={"key": "shared/reference.bin"})
        assert resp.status_code == 404
    finally:
        await engine.dispose()


async def test_object_route_404s_on_a_missing_key_not_a_500(
    object_route_app, database_url: str
):
    app, _file_store = object_route_app
    app.dependency_overrides[get_current_user] = lambda: OWNER
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    app.dependency_overrides[get_db] = lambda: factory()

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
            resp = await http.get(
                "/files/object",
                params={"key": f"tenants/{TENANT_A}/users/{OWNER.sub}/uploads/missing.png"},
            )
        assert resp.status_code == 404
    finally:
        await engine.dispose()


# ── _get_meta against a real row ─────────────────────────────────────────────


@pytest.fixture
async def db(database_url: str):
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


async def test_get_meta_owner_reads_own_file(db: AsyncSession):
    owner = User(id=uuid.uuid4(), identifier=f"owner-{uuid.uuid4()}")
    claims = AuthClaims(sub=str(owner.id), tenant_id=TENANT_A)
    db.add(owner)
    await db.commit()
    meta = _meta(object_key=f"anything/secret-{uuid.uuid4()}.pdf", user_id=owner.id)
    db.add(meta)
    await db.commit()
    try:
        found = await _get_meta(meta.id, db, claims)
        assert found.id == meta.id
    finally:
        await db.delete(meta)
        await db.delete(owner)
        await db.commit()


async def test_get_meta_stranger_gets_404(db: AsyncSession):
    """The regression test: a stranger must not be able to download another
    user's file by id."""
    from fastapi import HTTPException

    owner = User(id=uuid.uuid4(), identifier=f"owner-{uuid.uuid4()}")
    db.add(owner)
    await db.commit()
    meta = _meta(object_key=f"anything/secret-{uuid.uuid4()}.pdf", user_id=owner.id)
    db.add(meta)
    await db.commit()
    try:
        with pytest.raises(HTTPException) as exc_info:
            await _get_meta(meta.id, db, STRANGER)
        assert exc_info.value.status_code == 404
    finally:
        await db.delete(meta)
        await db.delete(owner)
        await db.commit()


async def test_get_meta_missing_file_also_404s(db: AsyncSession):
    """Missing-vs-forbidden must be indistinguishable — same 404 either way,
    so the endpoint can't be used to probe which file ids exist."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await _get_meta(uuid.uuid4(), db, STRANGER)
    assert exc_info.value.status_code == 404


# ── Route-level: proves the Depends chain is actually wired ─────────────────
#
# httpx.AsyncClient + ASGITransport, not the sync starlette TestClient — the
# latter runs the app in a separate thread with its own event loop, and an
# asyncpg connection created in this test's loop then used from that other
# loop raises "attached to a different loop". Staying on one loop avoids it.


@pytest.fixture
def app_with_overrides():
    app = FastAPI()
    app.include_router(router)

    file_store = InMemoryFileStore()
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


async def _seed_file(db_session_factory, file_store, *, owner: AuthClaims):
    owner_uuid = uuid.uuid4()
    object_key = f"anything/secret-{uuid.uuid4()}.pdf"
    await file_store.upload(object_key, b"pdf bytes", content_type="application/pdf")
    async with db_session_factory() as session:
        session.add(User(id=owner_uuid, identifier=f"owner-{owner_uuid}"))
        await session.commit()
        meta = _meta(object_key=object_key, user_id=owner_uuid)
        session.add(meta)
        await session.commit()
        await session.refresh(meta)
        return meta.id, owner_uuid


async def test_route_download_denies_stranger_with_404(
    app_with_overrides, database_url: str
):
    app, file_store = app_with_overrides
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    app.dependency_overrides[get_db] = lambda: factory()
    app.dependency_overrides[get_current_user] = lambda: STRANGER
    file_id, _owner_uuid = await _seed_file(factory, file_store, owner=OWNER)

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            resp = await http.get(f"/files/{file_id}/download")
        assert resp.status_code == 404
    finally:
        async with factory() as session:
            row = await session.get(FileMetadata, file_id)
            if row is not None:
                await session.delete(row)
                owner_row = await session.get(User, _owner_uuid)
                if owner_row is not None:
                    await session.delete(owner_row)
                await session.commit()
        await engine.dispose()


async def test_route_download_allows_owner(app_with_overrides, database_url: str):
    app, file_store = app_with_overrides
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    app.dependency_overrides[get_db] = lambda: factory()
    file_id, owner_uuid = await _seed_file(factory, file_store, owner=OWNER)
    app.dependency_overrides[get_current_user] = lambda: AuthClaims(
        sub=str(owner_uuid), tenant_id=TENANT_A
    )

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as http:
            resp = await http.get(f"/files/{file_id}/download")
        assert resp.status_code == 200
        assert resp.content == b"pdf bytes"
    finally:
        async with factory() as session:
            row = await session.get(FileMetadata, file_id)
            if row is not None:
                await session.delete(row)
            owner_row = await session.get(User, owner_uuid)
            if owner_row is not None:
                await session.delete(owner_row)
            await session.commit()
        await engine.dispose()
