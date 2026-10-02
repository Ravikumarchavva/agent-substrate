"""GDPR erasure orchestrator — erase_user/erase_tenant against a real DB.

No coverage existed for this before: the single place erasure logic lives
(``routes/gdpr.py`` is a thin dependency-wiring shell around these two
functions) had zero tests, despite being the compliance-critical path.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from substrate_cloud.gdpr.eraser import erase_tenant, erase_user
from substrate_cloud.config import SubstrateConfig
from substrate_cloud.monolith.models import FileMetadata, Thread, User
from substrate.testing.runtime import runtime_store


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
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    yield factory
    await engine.dispose()


@pytest.fixture
async def db(db_factory):
    async with db_factory() as session:
        yield session
        await session.rollback()


@pytest.fixture
def cfg(tmp_path):
    return SubstrateConfig()


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
        assert summary.documents_deleted == 0
        assert own_key not in store.objects
        assert (
            f"tenants/{tenant_id}/users/{user_uuid}/uploads/c.txt" not in store.objects
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
async def test_erase_user_also_clears_never_sent_pending_attachments(
    db: AsyncSession, db_factory, cfg
):
    """A composer attachment never touches `store` until the message
    carrying it is actually sent (routes/files.py) — before wiring
    pending_store in, one staged but never sent survived erasure entirely."""
    tenant_id = f"tenant-{uuid.uuid4()}"
    user_uuid = uuid.uuid4()
    db.add(User(id=user_uuid, identifier=f"owner-{user_uuid}@example.com"))
    thread = Thread(
        id=uuid.uuid4(), user_identifier=str(user_uuid), tenant_id=tenant_id
    )
    db.add(thread)
    await db.commit()

    store = FakeStore()
    pending_store = FakeStore()
    pending_key = (
        f"tenants/{tenant_id}/users/{user_uuid}/conversations/"
        f"{thread.id}/workspace/shared/never-sent.txt"
    )
    pending_store.objects[pending_key] = b"draft"

    try:
        summary = await erase_user(
            db,
            store=store,
            redis=None,
            tenant_id=tenant_id,
            user_id=str(user_uuid),
            cfg=cfg,
            pending_store=pending_store,
        )
        assert pending_key not in pending_store.objects
        assert summary.objects_deleted >= 1
    finally:
        row = await db.get(Thread, thread.id)
        if row is not None:
            await db.delete(row)
        user_row = await db.get(User, user_uuid)
        if user_row is not None:
            await db.delete(user_row)
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
    key_a = f"tenants/{tenant_a}/users/u1/conversations/{thread_a.id}/workspace/shared/a.txt"
    key_b = f"tenants/{tenant_b}/users/u2/conversations/{thread_b.id}/workspace/shared/b.txt"
    store.objects[key_a] = b"a"
    store.objects[key_b] = b"b"

    try:
        summary = await erase_tenant(
            db, store=store, redis=None, tenant_id=tenant_a, cfg=cfg
        )

        assert summary.conversations_deleted == 1
        assert key_a not in store.objects
        assert key_b in store.objects  # a different tenant, untouched
        assert summary.documents_deleted == 0
        async with db_factory() as verify:
            assert await verify.get(Thread, thread_a.id) is None
            assert await verify.get(Thread, thread_b.id) is not None
    finally:
        for tid in (thread_a.id, thread_b.id):
            row = await db.get(Thread, tid)
            if row is not None:
                await db.delete(row)
        await db.commit()


@pytest.mark.requires_postgres
async def test_erasure_reaches_long_term_memory_and_the_run_journal(
    db: AsyncSession, db_factory, cfg, tmp_path
):
    """A deletion request must reach the raw conversation in the run journal and the person's
    long-term memory, not only the relational rows: the journal is where the text actually lives."""
    from substrate.types import Actor
    from datetime import datetime, timezone

    from substrate.runtime import Commit, Complete, NewEntry, RunSpec
    from substrate.stores import MemoryNamespace, MemoryRecord
    from substrate.stores import Store

    tenant = f"tenant-{uuid.uuid4()}"
    thread = Thread(id=uuid.uuid4(), user_identifier="alice", tenant_id=tenant)
    other = Thread(id=uuid.uuid4(), user_identifier="bob", tenant_id=tenant)
    db.add_all([thread, other])
    await db.commit()

    memory = Store.at(tmp_path / "mem").memory
    await memory.save(
        MemoryRecord.from_text(
            "alice-secret-fact",
            namespace=MemoryNamespace(tenant_id=tenant, user_id="alice"),
        )
    )
    await memory.save(
        MemoryRecord.from_text(
            "bob-fact", namespace=MemoryNamespace(tenant_id=tenant, user_id="bob")
        )
    )

    runtime = runtime_store(tmp_path / "rt.sqlite3")
    await runtime.start()
    try:
        agent = Actor("agent", "a")
        for t, text in ((thread, "alice-secret-message"), (other, "bob-message")):
            await runtime.create_run(
                RunSpec(agent=agent, tenant=tenant, thread_id=str(t.id))
            )
            (lease,) = await runtime.lease(
                worker_id="w", capacity=1, lease_s=30, now=datetime.now(timezone.utc)
            )
            await runtime.commit(
                lease,
                Commit(
                    entries=(NewEntry(kind="user.message", payload={"text": text}),),
                    outcome=Complete(),
                ),
            )

        summary = await erase_user(
            db,
            store=FakeStore(),
            redis=None,
            tenant_id=tenant,
            user_id="alice",
            cfg=cfg,
            memory_store=memory,
            runtime_store=runtime,
        )

        assert summary.memories_deleted == 1 and summary.runs_deleted == 1
        remaining = [
            str(e.payload)
            for run in await runtime.find_runs(active_only=False)
            for e in await runtime.read_events(run.run_id)
        ]
        assert not any("alice-secret-message" in r for r in remaining), (
            "the raw conversation survived erasure"
        )
        assert any("bob-message" in r for r in remaining), (
            "someone else's conversation was erased"
        )
        raw = b"".join(
            p.read_bytes()
            for p in (tmp_path).rglob("*")
            if p.is_file() and "mem" in str(p)
        )
        assert b"alice-secret-fact" not in raw and b"bob-fact" in raw
    finally:
        await runtime.aclose()
        for tid in (thread.id, other.id):
            row = await db.get(Thread, tid)
            if row is not None:
                await db.delete(row)
        await db.commit()


@pytest.mark.requires_postgres
async def test_erasure_reaches_the_conversation_history_tasks_vectors_and_graph(
    db: AsyncSession, db_factory, cfg, tmp_path
):
    """The text of what was said lives in the thread DAG, and a session's notes in vectors and the graph: a
    deletion request that left them behind did not satisfy it."""
    from substrate.stores import Document, Entity, MessageNode, Store
    from substrate.types import ChatMessage, Role

    tenant, other_tenant = f"tenant-{uuid.uuid4()}", f"tenant-{uuid.uuid4()}"
    thread = Thread(id=uuid.uuid4(), user_identifier="alice", tenant_id=tenant)
    db.add(thread)
    await db.commit()

    folder = Store.at(tmp_path / "store")

    async def fill(t: str, conversation: str) -> str:
        view = folder.tenant(t)
        node = MessageNode(
            session_id=conversation,
            payload=ChatMessage(role=Role.USER, content="alice-secret-sentence"),
        )
        await view.threads.append_node(node)
        await view.tasks.create_task_list(conversation, ["a task"])
        await view.vectors.add(
            [Document.from_text("alice-secret-sentence", id="d", embedding=[1.0, 0.0])],
            collection="kb",
        )
        await view.graph.add_entities([Entity(id="e", label="P", name="alice")])
        return node.id

    mine = await fill(tenant, str(thread.id))
    kept_node = await fill(other_tenant, "kept")

    try:
        user = await erase_user(
            db,
            store=FakeStore(),
            redis=None,
            tenant_id=tenant,
            user_id="alice",
            cfg=cfg,
            folder=folder,
        )
        assert user.thread_nodes_deleted == 1 and user.task_boards_deleted == 1
        assert (
            await folder.tenant(tenant).tasks.get_by_conversation(str(thread.id))
            is None
        )

        whole = await erase_tenant(
            db, store=FakeStore(), redis=None, tenant_id=tenant, cfg=cfg, folder=folder
        )
        assert whole.vectors_deleted == 1 and whole.graph_entities_deleted == 1
        assert await folder.tenant(tenant).vectors.list_collections() == []
        assert await folder.tenant(tenant).threads.get_node(mine) is None
        assert (await folder.tenant(tenant).graph.get_neighbors("e")).entities == ()
        # another tenant's conversation, tasks, vectors and graph are untouched
        kept = folder.tenant(other_tenant)
        assert await kept.tasks.get_by_conversation("kept") is not None
        assert await kept.vectors.list_collections() == ["kb"]
        assert await kept.threads.get_node(kept_node) is not None
    finally:
        row = await db.get(Thread, thread.id)
        if row is not None:
            await db.delete(row)
            await db.commit()
        await folder.aclose()


async def test_erasing_a_user_removes_the_documents_their_conversations_were_given(
    tmp_path,
):
    """The catalog rows and the bundles of the user's conversations go; another user's, in the same tenant, stay."""
    from substrate.documents import ExtractedPage, ExtractionResult, Library
    from substrate.stores import Store
    from substrate.workspace.layout import conversation_documents_prefix, user_prefix
    from substrate_cloud.gdpr.eraser import _erase_documents

    store = Store.at(tmp_path / "store")
    await store.start()
    try:
        library = Library(store)
        doc = ExtractionResult(
            pages=[ExtractedPage(page_number=1, text="t")],
            markdown="<!-- page 1 -->\n\n" + "word " * 400,
            engine="t",
        )
        mine = conversation_documents_prefix("t1", "u1", "c1")
        theirs = conversation_documents_prefix("t1", "u2", "c2")
        await library.add(doc, "a.md", collection=mine)
        await library.add(doc, "b.md", collection=theirs)

        assert await _erase_documents(store, store.files, user_prefix("t1", "u1")) == 1

        assert (await library.list(collection=mine)).documents == ()
        assert len((await library.list(collection=theirs)).documents) == 1
        assert await store.files.list_prefix(mine + "/") == []
        assert await store.files.list_prefix(theirs + "/") != []
        assert (
            await _erase_documents(None, store.files, user_prefix("t1", "u2")) == 0
        )  # no folder store: nothing to erase
    finally:
        await store.aclose()
