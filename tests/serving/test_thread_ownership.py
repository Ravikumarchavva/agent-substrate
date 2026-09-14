"""Thread-ownership enforcement (IDOR regression tests).

Any route resolving a caller-supplied thread_id must go through
``get_owned_thread`` — these tests pin the service-level contract: owner
passes, foreign user gets None (routes 404), cross-tenant gets None even
for the same user id, a platform_admin bypasses entirely, a tenant_admin
sees any thread in its own tenant but not another's, and a soft-deleted
thread is hidden from its owner but still visible via the admin bypass.

Login is required to create a thread (routes/threads.py stamps owner +
tenant on every create), so there is no unowned/legacy-claim case anymore —
removed along with the matching RLS `tenant_id IS NULL` escape hatch
(rls.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from substrate.serving.monolith.services.thread_service import (
    create_thread,
    delete_thread,
    get_owned_thread,
    list_threads,
    update_thread,
)
from substrate.serving.shared.auth.claims import AuthClaims

TENANT_A = "tenant-a"
TENANT_B = "tenant-b"
OWNER = AuthClaims(sub="owner-user", tenant_id=TENANT_A)
STRANGER = AuthClaims(sub="stranger-user", tenant_id=TENANT_A)
CROSS_TENANT_OWNER = AuthClaims(sub="owner-user", tenant_id=TENANT_B)
ADMIN = AuthClaims(sub="admin-user", role="platform_admin", tenant_id=TENANT_B)
TENANT_ADMIN = AuthClaims(sub="tenant-admin-user", role="tenant_admin", tenant_id=TENANT_A)
CROSS_TENANT_ADMIN = AuthClaims(sub="tenant-admin-user", role="tenant_admin", tenant_id=TENANT_B)


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


async def test_owner_can_access_own_thread(db: AsyncSession) -> None:
    thread = await create_thread(
        db, name="mine", user_identifier=OWNER.sub, tenant_id=OWNER.tenant_id
    )
    try:
        found = await get_owned_thread(db, thread.id, OWNER)
        assert found is not None
        assert found.id == thread.id
    finally:
        await delete_thread(db, thread.id)
        await db.commit()


async def test_stranger_gets_none_for_foreign_thread(db: AsyncSession) -> None:
    thread = await create_thread(
        db, name="mine", user_identifier=OWNER.sub, tenant_id=OWNER.tenant_id
    )
    try:
        assert await get_owned_thread(db, thread.id, STRANGER) is None
    finally:
        await delete_thread(db, thread.id)
        await db.commit()


async def test_cross_tenant_gets_none_even_for_same_user_id(db: AsyncSession) -> None:
    """Same ``sub``, different ``tenant_id`` (e.g. the same email exists as
    a separate account in another tenant) must not resolve — ownership is
    (user, tenant), not just user."""
    thread = await create_thread(
        db, name="mine", user_identifier=OWNER.sub, tenant_id=OWNER.tenant_id
    )
    try:
        assert await get_owned_thread(db, thread.id, CROSS_TENANT_OWNER) is None
    finally:
        await delete_thread(db, thread.id)
        await db.commit()


async def test_platform_admin_bypasses_ownership(db: AsyncSession) -> None:
    thread = await create_thread(
        db, name="mine", user_identifier=OWNER.sub, tenant_id=OWNER.tenant_id
    )
    try:
        found = await get_owned_thread(db, thread.id, ADMIN)
        assert found is not None
    finally:
        await delete_thread(db, thread.id)
        await db.commit()


async def test_tenant_admin_sees_same_tenant_not_cross_tenant(db: AsyncSession) -> None:
    thread = await create_thread(
        db, name="mine", user_identifier=OWNER.sub, tenant_id=OWNER.tenant_id
    )
    try:
        assert await get_owned_thread(db, thread.id, TENANT_ADMIN) is not None
        assert await get_owned_thread(db, thread.id, CROSS_TENANT_ADMIN) is None
    finally:
        await delete_thread(db, thread.id)
        await db.commit()


async def test_deleted_thread_hidden_from_owner_visible_to_admin(db: AsyncSession) -> None:
    thread = await create_thread(
        db, name="mine", user_identifier=OWNER.sub, tenant_id=OWNER.tenant_id
    )
    await delete_thread(db, thread.id)
    await db.commit()
    try:
        assert await get_owned_thread(db, thread.id, OWNER) is None
        found = await get_owned_thread(db, thread.id, ADMIN)
        assert found is not None
        assert found.deleted_at is not None
    finally:
        await db.commit()


async def test_list_threads_scoped_by_owner(db: AsyncSession) -> None:
    mine = await create_thread(
        db, name="mine-scoped", user_identifier=OWNER.sub, tenant_id=OWNER.tenant_id
    )
    theirs = await create_thread(
        db, name="theirs-scoped", user_identifier=STRANGER.sub, tenant_id=STRANGER.tenant_id
    )
    try:
        rows = await list_threads(db, user_identifier=OWNER.sub, limit=200)
        ids = {str(r["id"]) for r in rows}
        assert str(mine.id) in ids
        assert str(theirs.id) not in ids
    finally:
        await delete_thread(db, mine.id)
        await delete_thread(db, theirs.id)
        await db.commit()


async def test_list_threads_excludes_deleted(db: AsyncSession) -> None:
    thread = await create_thread(
        db, name="to-delete", user_identifier=OWNER.sub, tenant_id=OWNER.tenant_id
    )
    await delete_thread(db, thread.id)
    await db.commit()
    try:
        rows = await list_threads(db, user_identifier=OWNER.sub, limit=200)
        assert str(thread.id) not in {str(r["id"]) for r in rows}
    finally:
        await db.commit()


async def test_update_thread_cannot_touch_the_lock(db: AsyncSession) -> None:
    """locked_at/locked_reason are real columns update_thread has no
    keyword for at all — PATCH /threads (which calls update_thread) can
    edit name/tags/metadata but structurally cannot clear a lock a user's
    own file-delete set, unlike the old metadata["locked"] flag it
    replaced, which PATCH's own metadata write could silently overwrite."""
    thread = await create_thread(
        db, name="lockable", user_identifier=OWNER.sub, tenant_id=OWNER.tenant_id
    )
    thread.locked_at = datetime.now(timezone.utc)
    thread.locked_reason = "A file was deleted from this conversation's storage: x.txt"
    await db.commit()
    try:
        updated = await update_thread(
            db, thread.id, name="renamed", metadata={"anything": "goes"}
        )
        assert updated is not None
        assert updated.name == "renamed"
        assert updated.locked_at is not None
        assert updated.locked_reason == "A file was deleted from this conversation's storage: x.txt"
    finally:
        await delete_thread(db, thread.id)
        await db.commit()
