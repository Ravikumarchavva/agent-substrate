"""File-ownership enforcement (IDOR regression tests).

``GET/DELETE /files/{file_id}/*`` used to have no ownership check at all —
any authenticated user could read/delete any file by id, which matters once
citations start putting file ids in the chat stream (routes/files.py,
routes/chat_context.py). These tests pin the fix at two levels: the pure
``_may_access`` predicate, and ``_get_meta`` against a real DB row (mirrors
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
from substrate.serving.monolith.models import FileMetadata
from substrate.serving.monolith.routes.files import _get_meta, _may_access, router
from substrate.serving.monolith.security.deps import get_current_user
from substrate.serving.shared.auth.claims import AuthClaims

OWNER = AuthClaims(sub="owner-user")
STRANGER = AuthClaims(sub="stranger-user")
ADMIN = AuthClaims(sub="admin-user", role="platform_admin")


def _meta(
    *,
    object_key: str,
    user_id: uuid.UUID | None = None,
    org_id: str | None = None,
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


# ── _may_access (pure — no DB) ───────────────────────────────────────────────


def test_owner_may_access_via_object_key_prefix():
    meta = _meta(object_key=f"users/{OWNER.sub}/uploads/secret.pdf")
    assert _may_access(meta, OWNER) is True


def test_stranger_denied_via_object_key_prefix():
    meta = _meta(object_key=f"users/{OWNER.sub}/uploads/secret.pdf")
    assert _may_access(meta, STRANGER) is False


def test_admin_bypasses_ownership():
    meta = _meta(object_key=f"users/{OWNER.sub}/uploads/secret.pdf")
    assert _may_access(meta, ADMIN) is True


def test_non_uuid_sub_owner_allowed_via_object_key_prefix():
    """upload_file leaves user_id NULL when claims.sub isn't a UUID — the
    object_key prefix must still grant access, or those users get locked out
    of files they uploaded themselves."""
    claims = AuthClaims(sub="google-oauth2|123456")
    meta = _meta(object_key=f"users/{claims.sub}/uploads/secret.pdf", user_id=None)
    assert _may_access(meta, claims) is True


def test_user_id_fallback_when_object_key_prefix_does_not_match():
    """Legacy row shape: object_key doesn't carry the prefix, user_id does."""
    owner_uuid = uuid.uuid4()
    claims = AuthClaims(sub=str(owner_uuid))
    meta = _meta(object_key="legacy/path/secret.pdf", user_id=owner_uuid)
    assert _may_access(meta, claims) is True


def test_cross_tenant_same_sub_denied():
    """Matching claims.sub isn't enough across a tenant boundary."""
    meta = _meta(object_key=f"users/{OWNER.sub}/uploads/secret.pdf", org_id="tenant-a")
    claims = AuthClaims(sub=OWNER.sub, tenant_id="tenant-b")
    assert _may_access(meta, claims) is False


def test_no_owner_signal_at_all_denied():
    meta = _meta(object_key="orphaned/secret.pdf", user_id=None)
    assert _may_access(meta, STRANGER) is False


# ── GET /files/object — key-based, no FileMetadata row involved at all ────────
#
# The target of tool-result `object:` attachment refs (RAG images, potentially
# code-interpreter charts) — ownership here is the key's own users/{sub}/
# prefix, not a DB lookup, so it gets its own fixture rather than _seed_file.


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


async def test_object_route_serves_the_owners_key(object_route_app):
    app, file_store = object_route_app
    await file_store.upload("users/owner-user/rag/f1/p1.png", b"PNGBYTES")
    app.dependency_overrides[get_current_user] = lambda: OWNER

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        resp = await http.get(
            "/files/object", params={"key": "users/owner-user/rag/f1/p1.png"}
        )
    assert resp.status_code == 200
    assert resp.content == b"PNGBYTES"


async def test_object_route_denies_a_key_under_another_users_prefix(object_route_app):
    app, file_store = object_route_app
    await file_store.upload("users/owner-user/rag/f1/p1.png", b"PNGBYTES")
    app.dependency_overrides[get_current_user] = lambda: STRANGER

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        resp = await http.get(
            "/files/object", params={"key": "users/owner-user/rag/f1/p1.png"}
        )
    # 404, not 403: this must not become an existence oracle for keys that
    # leak into logs/URLs (same rationale as _get_meta above).
    assert resp.status_code == 404


async def test_object_route_404s_on_a_missing_key_not_a_500(object_route_app):
    app, _file_store = object_route_app
    app.dependency_overrides[get_current_user] = lambda: OWNER

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        resp = await http.get(
            "/files/object", params={"key": "users/owner-user/rag/f1/missing.png"}
        )
    assert resp.status_code == 404


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
    meta = _meta(object_key=f"users/{OWNER.sub}/uploads/secret-{uuid.uuid4()}.pdf")
    db.add(meta)
    await db.commit()
    try:
        found = await _get_meta(meta.id, db, OWNER)
        assert found.id == meta.id
    finally:
        await db.delete(meta)
        await db.commit()


async def test_get_meta_stranger_gets_404(db: AsyncSession):
    """The regression test: a stranger must not be able to download another
    user's file by id."""
    from fastapi import HTTPException

    meta = _meta(object_key=f"users/{OWNER.sub}/uploads/secret-{uuid.uuid4()}.pdf")
    db.add(meta)
    await db.commit()
    try:
        with pytest.raises(HTTPException) as exc_info:
            await _get_meta(meta.id, db, STRANGER)
        assert exc_info.value.status_code == 404
    finally:
        await db.delete(meta)
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
    object_key = f"users/{owner.sub}/uploads/secret-{uuid.uuid4()}.pdf"
    await file_store.upload(object_key, b"pdf bytes", content_type="application/pdf")
    async with db_session_factory() as session:
        meta = _meta(object_key=object_key)
        session.add(meta)
        await session.commit()
        await session.refresh(meta)
        return meta.id


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
    file_id = await _seed_file(factory, file_store, owner=OWNER)

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
                await session.commit()
        await engine.dispose()


async def test_route_download_allows_owner(app_with_overrides, database_url: str):
    app, file_store = app_with_overrides
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    app.dependency_overrides[get_db] = lambda: factory()
    app.dependency_overrides[get_current_user] = lambda: OWNER
    file_id = await _seed_file(factory, file_store, owner=OWNER)

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
                await session.commit()
        await engine.dispose()
