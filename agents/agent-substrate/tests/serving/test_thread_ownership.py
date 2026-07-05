"""Thread-ownership enforcement (IDOR regression tests).

Any route resolving a caller-supplied thread_id must go through
``get_owned_thread`` — these tests pin the service-level contract:
owner passes, foreign user gets None (routes 404), admin bypasses,
and unowned legacy threads are claimed on first access.
"""

from __future__ import annotations

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
)
from substrate.serving.shared.auth.claims import AuthClaims

OWNER = AuthClaims(sub="owner-user")
STRANGER = AuthClaims(sub="stranger-user")
ADMIN = AuthClaims(sub="admin-user", role="platform_admin")


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
    thread = await create_thread(db, name="mine", user_identifier=OWNER.sub)
    try:
        found = await get_owned_thread(db, thread.id, OWNER)
        assert found is not None
        assert found.id == thread.id
    finally:
        await delete_thread(db, thread.id)
        await db.commit()


async def test_stranger_gets_none_for_foreign_thread(db: AsyncSession) -> None:
    thread = await create_thread(db, name="mine", user_identifier=OWNER.sub)
    try:
        assert await get_owned_thread(db, thread.id, STRANGER) is None
    finally:
        await delete_thread(db, thread.id)
        await db.commit()


async def test_admin_bypasses_ownership(db: AsyncSession) -> None:
    thread = await create_thread(db, name="mine", user_identifier=OWNER.sub)
    try:
        found = await get_owned_thread(db, thread.id, ADMIN)
        assert found is not None
    finally:
        await delete_thread(db, thread.id)
        await db.commit()


async def test_unowned_legacy_thread_claimed_on_access(db: AsyncSession) -> None:
    thread = await create_thread(db, name="legacy")  # user_identifier=None
    try:
        found = await get_owned_thread(db, thread.id, OWNER)
        assert found is not None
        assert found.user_identifier == OWNER.sub  # claimed in place
        # After the claim, a different user is locked out.
        assert await get_owned_thread(db, thread.id, STRANGER) is None
    finally:
        await delete_thread(db, thread.id)
        await db.commit()


async def test_list_threads_scoped_by_owner(db: AsyncSession) -> None:
    mine = await create_thread(db, name="mine-scoped", user_identifier=OWNER.sub)
    theirs = await create_thread(db, name="theirs-scoped", user_identifier=STRANGER.sub)
    try:
        rows = await list_threads(db, user_identifier=OWNER.sub, limit=200)
        ids = {str(r["id"]) for r in rows}
        assert str(mine.id) in ids
        assert str(theirs.id) not in ids
    finally:
        await delete_thread(db, mine.id)
        await delete_thread(db, theirs.id)
        await db.commit()
