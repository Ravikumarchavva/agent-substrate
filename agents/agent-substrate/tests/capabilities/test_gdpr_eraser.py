"""GDPR erasure orchestrator — erase_user/erase_tenant against a real DB.

No coverage existed for this before: the single place erasure logic lives
(``routes/gdpr.py`` is a thin dependency-wiring shell around these two
functions) had zero tests, despite being the compliance-critical path.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from substrate.capabilities.gdpr.eraser import erase_tenant, erase_user
from substrate.config import SubstrateConfig
from substrate.serving.monolith.models import FileMetadata, Thread, User


class FakeStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def delete_prefix(self, prefix: str) -> int:
        keys = [k for k in self.objects if k.startswith(prefix)]
        for k in keys:
            del self.objects[k]
        return len(keys)


class FakeRedis:
    def __init__(self, keys: list[str]) -> None:
        self.store = {k: b"1" for k in keys}

    async def scan_iter(self, match: str = "*"):
        for key in list(self.store):
            yield key.encode()

    async def delete(self, key) -> int:
        key = key.decode() if isinstance(key, bytes) else key
        return 1 if self.store.pop(key, None) is not None else 0


@pytest.fixture
async def db_factory(database_url: str):
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.fixture
async def db(db_factory):
    async with db_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def cfg(tmp_path):
    return SubstrateConfig(SESSION_INDEX_LOCAL_PATH=str(tmp_path / "session-index"))


@pytest.mark.requires_postgres
async def test_erase_user_deletes_threads_metadata_and_the_user_row(
    db: AsyncSession, db_factory, cfg
):
    tenant_id = f"tenant-{uuid.uuid4()}"
    user_uuid = uuid.uuid4()
    other_user_uuid = uuid.uuid4()

    db.add(User(id=user_uuid, identifier=f"owner-{user_uuid}@example.com"))
    db.add(User(id=other_user_uuid, identifier=f"other-{other_user_uuid}@example.com"))
    thread = Thread(
        id=uuid.uuid4(), user_identifier=str(user_uuid), tenant_id=tenant_id
    )
    other_thread = Thread(
        id=uuid.uuid4(), user_identifier=str(other_user_uuid), tenant_id=tenant_id
    )
    db.add(thread)
    db.add(other_thread)
    await db.commit()

    own_key = (
        f"tenants/{tenant_id}/users/{user_uuid}/conversations/"
        f"{thread.id}/workspace/shared/a.txt"
    )
    other_key = (
        f"tenants/{tenant_id}/users/{other_user_uuid}/conversations/"
        f"{other_thread.id}/workspace/shared/b.txt"
    )
    db.add(
        FileMetadata(
            id=uuid.uuid4(),
            object_key=own_key,
            original_name="a.txt",
            content_type="text/plain",
            size_bytes=1,
            org_id=tenant_id,
            user_id=user_uuid,
            thread_id=thread.id,
        )
    )
    db.add(
        FileMetadata(
            id=uuid.uuid4(),
            object_key=other_key,
            original_name="b.txt",
            content_type="text/plain",
            size_bytes=1,
            org_id=tenant_id,
            user_id=other_user_uuid,
            thread_id=other_thread.id,
        )
    )
    await db.commit()

    store = FakeStore()
    store.objects[own_key] = b"a"
    store.objects[f"tenants/{tenant_id}/users/{user_uuid}/uploads/c.txt"] = b"c"
    store.objects[other_key] = b"b"
    redis = FakeRedis([f"session:state:{thread.id}", "unrelated:key"])

    try:
        summary = await erase_user(
            db,
            store=store,
            redis=redis,
            tenant_id=tenant_id,
            user_id=str(user_uuid),
            cfg=cfg,
        )
        assert summary.conversations_deleted == 1
        assert summary.metadata_rows_deleted == 1
        assert summary.session_index_tables_deleted == 0
        assert own_key not in store.objects
        assert (
            f"tenants/{tenant_id}/users/{user_uuid}/uploads/c.txt"
            not in store.objects
        )
        assert other_key in store.objects  # the other user's file survives
        assert f"session:state:{thread.id}" not in redis.store
        assert "unrelated:key" in redis.store

        # A fresh session — not this one's identity map, which the erase's
        # Core bulk deletes don't expire — matching what a separate,
        # request-scoped session (the real usage pattern) would see.
        async with db_factory() as verify:
            assert await verify.get(Thread, thread.id) is None
            assert await verify.get(Thread, other_thread.id) is not None
            assert await verify.get(User, user_uuid) is None
            assert await verify.get(User, other_user_uuid) is not None
    finally:
        for tid in (thread.id, other_thread.id):
            row = await db.get(Thread, tid)
            if row is not None:
                await db.delete(row)
        for uid in (user_uuid, other_user_uuid):
            row = await db.get(User, uid)
            if row is not None:
                await db.delete(row)
        await db.commit()


@pytest.mark.requires_postgres
async def test_erase_user_with_non_uuid_sub_skips_the_user_table(
    db: AsyncSession, db_factory, cfg
):
    """A sub that isn't a UUID (e.g. an external platform's own id format)
    has no substrate `users` row at all — erasure must not crash trying to
    delete one, and must still remove the thread/metadata rows it does own."""
    tenant_id = f"tenant-{uuid.uuid4()}"
    sub = f"external|{uuid.uuid4()}"
    thread = Thread(id=uuid.uuid4(), user_identifier=sub, tenant_id=tenant_id)
    db.add(thread)
    await db.commit()

    store = FakeStore()
    try:
        summary = await erase_user(
            db, store=store, redis=None, tenant_id=tenant_id, user_id=sub, cfg=cfg
        )
        assert summary.conversations_deleted == 1
        async with db_factory() as verify:
            assert await verify.get(Thread, thread.id) is None
    finally:
        async with db_factory() as cleanup:
            row = await cleanup.get(Thread, thread.id)
            if row is not None:
                await cleanup.delete(row)
                await cleanup.commit()


@pytest.mark.requires_postgres
async def test_erase_tenant_deletes_every_thread_in_that_tenant_only(
    db: AsyncSession, db_factory, cfg
):
    tenant_a = f"tenant-{uuid.uuid4()}"
    tenant_b = f"tenant-{uuid.uuid4()}"
    thread_a = Thread(id=uuid.uuid4(), user_identifier="u1", tenant_id=tenant_a)
    thread_b = Thread(id=uuid.uuid4(), user_identifier="u2", tenant_id=tenant_b)
    db.add(thread_a)
    db.add(thread_b)
    await db.commit()

    store = FakeStore()
    key_a = f"tenants/{tenant_a}/conversations/{thread_a.id}/workspace/shared/a.txt"
    key_b = f"tenants/{tenant_b}/conversations/{thread_b.id}/workspace/shared/b.txt"
    store.objects[key_a] = b"a"
    store.objects[key_b] = b"b"

    try:
        summary = await erase_tenant(
            db, store=store, redis=None, tenant_id=tenant_a, cfg=cfg
        )

        assert summary.conversations_deleted == 1
        assert key_a not in store.objects
        assert key_b in store.objects  # a different tenant, untouched
        assert summary.session_index_tables_deleted == 0
        async with db_factory() as verify:
            assert await verify.get(Thread, thread_a.id) is None
            assert await verify.get(Thread, thread_b.id) is not None
    finally:
        for tid in (thread_a.id, thread_b.id):
            row = await db.get(Thread, tid)
            if row is not None:
                await db.delete(row)
        await db.commit()
