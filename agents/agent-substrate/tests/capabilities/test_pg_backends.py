"""Integration tests for Stage 1 Postgres backends.

Requires running Postgres.
Skip automatically when DATABASE_URL is not reachable.

Run with infra up:
    make infra-up
    uv run pytest tests/capabilities/test_pg_backends.py -v
"""

from __future__ import annotations

import os
import asyncio
from typing import TYPE_CHECKING

import pytest

pytestmark = [pytest.mark.requires_postgres]

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

_PG_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agentdb"
).replace("+asyncpg", "")


async def _pg_pool():
    try:
        import asyncpg

        pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=3)
        return pool
    except Exception:
        return None


@pytest.fixture()
async def pg_pool():
    pool = await _pg_pool()
    if pool is None:
        pytest.skip("Postgres not reachable")
    yield pool
    await pool.close()


# ---------------------------------------------------------------------------
# EventLog
# ---------------------------------------------------------------------------


async def test_pg_event_log_append_and_read(pg_pool) -> None:
    from substrate.infrastructure.runtime import EventLog
    from substrate.kernel.runtime.log_entry import RunLogEntry
    from substrate.kernel.runtime.ids import new_run_id

    log = EventLog(pg_pool)
    await log.setup()

    run_id = new_run_id()
    entry = RunLogEntry(run_id=run_id, seq=0, kind="run.started", payload={"msg": "hi"})
    seq = await log.append(run_id, entry, expected_seq=-1)
    assert seq == 0

    entries = [e async for e in log.read(run_id)]
    assert len(entries) == 1
    assert entries[0].kind == "run.started"
    assert entries[0].payload == {"msg": "hi"}


async def test_pg_event_log_occ_raises(pg_pool) -> None:
    from substrate.infrastructure.runtime import EventLog
    from substrate.kernel.runtime.log_entry import RunLogEntry
    from substrate.kernel.core.errors import ConcurrentAppendError
    from substrate.kernel.runtime.ids import new_run_id

    log = EventLog(pg_pool)
    await log.setup()

    run_id = new_run_id()
    e0 = RunLogEntry(run_id=run_id, seq=0, kind="run.started", payload={})
    await log.append(run_id, e0, expected_seq=-1)

    e1 = RunLogEntry(run_id=run_id, seq=1, kind="msg.received", payload={})
    with pytest.raises(ConcurrentAppendError):
        await log.append(run_id, e1, expected_seq=-1)  # wrong expected_seq


async def test_pg_event_log_last_seq(pg_pool) -> None:
    from substrate.infrastructure.runtime import EventLog
    from substrate.kernel.runtime.log_entry import RunLogEntry
    from substrate.kernel.runtime.ids import new_run_id

    log = EventLog(pg_pool)
    await log.setup()

    run_id = new_run_id()
    assert await log.last_seq(run_id) == -1

    e = RunLogEntry(run_id=run_id, seq=0, kind="run.started", payload={})
    await log.append(run_id, e, expected_seq=-1)
    assert await log.last_seq(run_id) == 0


async def test_pg_event_log_tail_yields_existing(pg_pool) -> None:
    from substrate.infrastructure.runtime import EventLog
    from substrate.kernel.runtime.log_entry import RunLogEntry
    from substrate.kernel.runtime.ids import new_run_id

    log = EventLog(pg_pool)
    await log.setup()

    run_id = new_run_id()
    for i in range(3):
        e = RunLogEntry(run_id=run_id, seq=i, kind=f"step.{i}", payload={})
        await log.append(run_id, e, expected_seq=i - 1)

    collected: list[str] = []

    async def drain():
        async for entry in log.tail(run_id):
            collected.append(entry.kind)
            if entry.kind == "step.2":
                break

    await asyncio.wait_for(drain(), timeout=2.0)
    assert collected == ["step.0", "step.1", "step.2"]


# ---------------------------------------------------------------------------
# Inbox
# ---------------------------------------------------------------------------


async def test_pg_inbox_deliver_and_drain(pg_pool) -> None:
    from substrate.infrastructure.runtime import Inbox
    from substrate.kernel.core.identity import AgentId
    from substrate.kernel.messaging.message import Message
    from substrate.kernel.core.content import TextBlock
    from substrate.kernel.core.content import ChatMessage, Role
    from substrate.kernel.messaging.message import ChatPayload

    inbox = Inbox(pg_pool)
    await inbox.setup()

    agent_id = AgentId(type="agent", key=f"test-inbox-agent-{id(object())}")
    msg = Message(
        target=agent_id,
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text="hello")])
        ),
    )

    delivered = await inbox.deliver(agent_id, msg)
    assert delivered is True

    duplicate = await inbox.deliver(agent_id, msg)
    assert duplicate is False

    msgs = await inbox.drain(agent_id)
    assert len(msgs) == 1
    assert msgs[0].id == msg.id

    await inbox.ack(agent_id, msg.id)
    assert await inbox.pending_count(agent_id) == 0


async def test_pg_inbox_nack_dead_letters(pg_pool) -> None:
    from substrate.infrastructure.runtime import Inbox
    from substrate.kernel.core.identity import AgentId
    from substrate.kernel.messaging.message import Message
    from substrate.kernel.core.content import TextBlock
    from substrate.kernel.core.content import ChatMessage, Role
    from substrate.kernel.messaging.message import ChatPayload

    inbox = Inbox(pg_pool, max_retries=2)
    await inbox.setup()

    agent_id = AgentId(type="agent", key=f"test-nack-agent-{id(object())}")
    msg = Message(
        target=agent_id,
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text="fail-me")])
        ),
    )
    await inbox.deliver(agent_id, msg)

    await inbox.nack(agent_id, msg.id, error="err1")
    await inbox.nack(agent_id, msg.id, error="err2")  # hits max_retries → dead-letter

    assert await inbox.pending_count(agent_id) == 0
    dead = await inbox.dead_letters(agent_id)
    assert len(dead) == 1
    assert dead[0].msg.id == msg.id
    assert dead[0].last_error == "err2"


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------


async def test_pg_scheduler_enqueue_and_lease(pg_pool) -> None:
    from substrate.infrastructure.runtime import Scheduler
    from substrate.kernel.runtime.ids import new_run_id, RunStatus
    from substrate.kernel.core.identity import AgentId

    sched = Scheduler(pg_pool)
    await sched.setup()

    run_id = new_run_id()
    agent_id = AgentId(type="agent", key="sched-test")
    sched.register_run(run_id, agent_id)
    await sched.enqueue(run_id, priority=5, tenant="test")

    leases = await sched.lease(worker_id="w1", capacity=100)
    our_lease = next((lease for lease in leases if lease.run_id == run_id), None)
    assert our_lease is not None

    status = await sched.get_status(run_id)
    assert status == RunStatus.RUNNING


async def test_pg_scheduler_coalescing(pg_pool) -> None:
    from substrate.infrastructure.runtime import Scheduler
    from substrate.kernel.runtime.ids import new_run_id
    from substrate.kernel.core.identity import AgentId

    sched = Scheduler(pg_pool)
    await sched.setup()

    run_id = new_run_id()
    agent_id = AgentId(type="agent", key=f"coalesce-{id(object())}")
    sched.register_run(run_id, agent_id)
    await sched.enqueue(run_id, priority=5, tenant="test")
    await sched.enqueue(run_id, priority=5, tenant="test")  # no-op

    leases = await sched.lease(worker_id="w1", capacity=10)
    run_ids = [lease.run_id for lease in leases if lease.run_id == run_id]
    assert len(run_ids) == 1


async def test_pg_scheduler_release_completed(pg_pool) -> None:
    from substrate.infrastructure.runtime import Scheduler
    from substrate.kernel.runtime.ids import new_run_id, RunStatus
    from substrate.kernel.core.identity import AgentId

    sched = Scheduler(pg_pool)
    await sched.setup()

    run_id = new_run_id()
    agent_id = AgentId(type="agent", key=f"release-{id(object())}")
    sched.register_run(run_id, agent_id)
    await sched.enqueue(run_id, priority=5, tenant="test")
    leases = await sched.lease(worker_id="w1", capacity=100)
    matching = [lease for lease in leases if lease.run_id == run_id]
    assert matching, f"run_id {run_id} not found in leases"
    target = matching[0]

    await sched.release(target, status=RunStatus.COMPLETED)
    assert await sched.get_status(run_id) == RunStatus.COMPLETED


# ---------------------------------------------------------------------------
# PgTaskStore
# ---------------------------------------------------------------------------


async def _pg_session_factory() -> "async_sessionmaker | None":
    """Return a SQLAlchemy async_sessionmaker pointed at the test PG instance."""
    try:
        from sqlalchemy.ext.asyncio import (
            create_async_engine,
            async_sessionmaker,
            AsyncSession,
        )

        url = _PG_URL.replace("postgresql://", "postgresql+asyncpg://")
        engine = create_async_engine(url, pool_pre_ping=True)
        factory = async_sessionmaker(
            engine, class_=AsyncSession, expire_on_commit=False
        )
        # quick connectivity check
        async with factory() as s:
            from sqlalchemy import text

            await s.execute(text("SELECT 1"))
        return factory
    except Exception:
        return None


async def test_pg_task_store_persist_and_reload() -> None:
    """Create a task list, mutate it, then reload through a fresh PgTaskStore instance
    (simulating a restart) — board should survive."""
    factory = await _pg_session_factory()
    if factory is None:
        pytest.skip("Postgres not reachable")

    from substrate.infrastructure.storage.pg_task_store import PgTaskStore

    conv_id = f"conv-{id(object())}"

    store1 = PgTaskStore(factory)
    await store1.setup()

    tl = await store1.create_task_list(conv_id, ["plan", "code", "test"], max_retries=2)
    await store1.update_status(tl.id, tl.tasks[0].id, "in_progress")
    await store1.update_status(tl.id, tl.tasks[0].id, "done")

    # Fresh store — simulates restart
    store2 = PgTaskStore(factory)
    reloaded = await store2.get_by_conversation(conv_id)
    assert reloaded is not None
    assert reloaded.conversation_id == conv_id
    assert len(reloaded.tasks) == 3
    done_tasks = [t for t in reloaded.tasks if t.status == "done"]
    assert len(done_tasks) == 1
    assert done_tasks[0].title == "plan"

    # Verify get_task_list by id also works
    by_id = await store2.get_task_list(tl.id)
    assert by_id is not None
    assert by_id.id == tl.id


async def test_pg_task_store_add_and_delete() -> None:
    """add_tasks and delete_task persist across store instances."""
    factory = await _pg_session_factory()
    if factory is None:
        pytest.skip("Postgres not reachable")

    from substrate.infrastructure.storage.pg_task_store import PgTaskStore

    conv_id = f"conv-add-{id(object())}"
    store = PgTaskStore(factory)
    await store.setup()

    tl = await store.create_task_list(conv_id, ["alpha"], max_retries=1)
    added = await store.add_tasks(tl.id, ["beta", "gamma"])
    assert len(added) == 2

    fresh = PgTaskStore(factory)
    reloaded = await fresh.get_by_conversation(conv_id)
    assert reloaded is not None
    assert len(reloaded.tasks) == 3

    deleted = await fresh.delete_task(tl.id, added[0].id)
    assert deleted is True

    after_delete = await fresh.get_task_list(tl.id)
    assert after_delete is not None
    assert len(after_delete.tasks) == 2


# ---------------------------------------------------------------------------
# PgVectorStore — configurable table_name
# ---------------------------------------------------------------------------


async def _pg_async_engine():
    try:
        from sqlalchemy.ext.asyncio import create_async_engine

        url = _PG_URL.replace("postgresql://", "postgresql+asyncpg://")
        engine = create_async_engine(url, pool_pre_ping=True)
        async with engine.connect():
            pass
        return engine
    except Exception:
        return None


def test_pg_vector_store_rejects_invalid_table_name() -> None:
    """table_name is interpolated directly into SQL (no ORM/param binding
    for identifiers) — must be validated at construction, not left to fail
    confusingly (or unsafely) at the first query."""
    from substrate.capabilities.vector.pgvector_store import PgVectorStore

    with pytest.raises(ValueError):
        PgVectorStore(
            session_factory=None, engine=None, table_name="not valid; DROP TABLE x"
        )


async def test_pg_vector_store_custom_table_name_is_isolated_from_default() -> None:
    """A second PgVectorStore with a different table_name must not share
    rows (or even its table) with the default-table instance — this is the
    whole point of the parameter: one Postgres vector(N) column has a fixed
    width, so a second embedding dimensionality needs its own table."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from substrate.capabilities.vector.pgvector_store import PgVectorStore
    from substrate.kernel.storage.vector import Document

    engine = await _pg_async_engine()
    if engine is None:
        pytest.skip("Postgres not reachable")
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # The default table (dimensions=384) may already exist from other tests
    # sharing this Postgres instance — ensure_table() is CREATE TABLE IF NOT
    # EXISTS, so it won't retroactively change an existing table's vector
    # width. Match that width here rather than assuming a fresh table.
    default_store = PgVectorStore(
        session_factory=session_factory, engine=engine, dimensions=384
    )
    custom_store = PgVectorStore(
        session_factory=session_factory,
        engine=engine,
        dimensions=3,
        table_name="vector_documents_test_images",
    )
    await default_store.ensure_table()
    await custom_store.ensure_table()

    default_vec = [0.1] * 384
    collection = f"isolation-test-{id(object())}"
    await default_store.add(
        [Document.from_text("in the default table", embedding=default_vec)],
        collection=collection,
    )
    await custom_store.add(
        [Document.from_text("in the custom table", embedding=[0.4, 0.5, 0.6])],
        collection=collection,
    )

    default_results = await default_store.search(
        default_vec, collection=collection, limit=10
    )
    custom_results = await custom_store.search(
        [0.4, 0.5, 0.6], collection=collection, limit=10
    )

    assert len(default_results) == 1
    assert default_results[0].to_text() == "in the default table"
    assert len(custom_results) == 1
    assert custom_results[0].to_text() == "in the custom table"

    await default_store.delete_collection(collection)
    await custom_store.delete_collection(collection)


async def test_pg_vector_store_rename_collection_rekeys_rows() -> None:
    """rename_collection moves every row from one collection to another —
    the mechanism LocalRagBackend.promote() uses to move a staged document
    into a thread's real collection without re-embedding."""
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from substrate.capabilities.vector.pgvector_store import PgVectorStore
    from substrate.kernel.storage.vector import Document

    engine = await _pg_async_engine()
    if engine is None:
        pytest.skip("Postgres not reachable")
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    # Match the default table's existing dimensionality (see the isolation
    # test above) rather than assuming a fresh table.
    store = PgVectorStore(
        session_factory=session_factory, engine=engine, dimensions=384
    )
    await store.ensure_table()

    vec = [0.1] * 384
    old_collection = f"staging-rename-test-{id(object())}"
    new_collection = f"promoted-rename-test-{id(object())}"
    await store.add(
        [
            Document.from_text("page one", embedding=vec),
            Document.from_text("page two", embedding=vec),
        ],
        collection=old_collection,
    )

    moved = await store.rename_collection(old_collection, new_collection)

    assert moved == 2
    assert await store.search(vec, collection=old_collection, limit=10) == []
    new_results = await store.search(vec, collection=new_collection, limit=10)
    assert {r.to_text() for r in new_results} == {"page one", "page two"}

    await store.delete_collection(new_collection)


async def test_pg_vector_store_rename_collection_noop_when_nothing_matches() -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from substrate.capabilities.vector.pgvector_store import PgVectorStore

    engine = await _pg_async_engine()
    if engine is None:
        pytest.skip("Postgres not reachable")
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    store = PgVectorStore(
        session_factory=session_factory, engine=engine, dimensions=384
    )
    await store.ensure_table()

    moved = await store.rename_collection(
        f"nonexistent-{id(object())}", f"also-nonexistent-{id(object())}"
    )
    assert moved == 0


async def test_pg_scheduler_find_run_for_agent(pg_pool) -> None:
    from substrate.infrastructure.runtime import Scheduler
    from substrate.kernel.runtime.ids import new_run_id, RunStatus
    from substrate.kernel.core.identity import AgentId

    sched = Scheduler(pg_pool)
    await sched.setup()

    agent_id = AgentId(type="agent", key="find-agent-test")
    run_id = new_run_id()
    sched.register_run(run_id, agent_id)
    await sched.enqueue(run_id, priority=5, tenant="test")

    result = await sched.find_run_for_agent(agent_id)
    assert result is not None
    rid, status = result
    assert rid == run_id
    assert status == RunStatus.PENDING


# ---------------------------------------------------------------------------
# PgVectorStore — hybrid_search / lexical_search
# ---------------------------------------------------------------------------


async def _hybrid_test_store():
    from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

    from substrate.capabilities.vector.pgvector_store import PgVectorStore

    engine = await _pg_async_engine()
    if engine is None:
        return None
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    # Match the default table's existing dimensionality (see the isolation
    # test above) rather than assuming a fresh table.
    store = PgVectorStore(
        session_factory=session_factory, engine=engine, dimensions=384
    )
    await store.ensure_table()
    return store


def _vec384(*nonzero: tuple[int, float]) -> list[float]:
    """A 384-dim vector (the shared default table's fixed width) with the
    given (index, value) pairs set, zero elsewhere."""
    v = [0.0] * 384
    for i, val in nonzero:
        v[i] = val
    return v


async def test_pg_vector_store_lexical_search_ranks_by_ts_rank() -> None:
    from substrate.kernel.storage.vector import Document

    store = await _hybrid_test_store()
    if store is None:
        pytest.skip("Postgres not reachable")
    collection = f"lexical-test-{id(object())}"
    await store.add(
        [
            Document.from_text(
                "quarterly revenue grew 20 percent", embedding=_vec384((0, 1.0))
            ),
            Document.from_text(
                "the weather was sunny in San Francisco", embedding=_vec384((0, 1.0))
            ),
        ],
        collection=collection,
    )

    results = await store.lexical_search("revenue", collection=collection, limit=10)

    assert len(results) == 1
    assert "revenue" in results[0].to_text()

    await store.delete_collection(collection)


async def test_pg_vector_store_hybrid_search_fuses_dense_and_lexical_rank() -> None:
    """Reproduces the exact scenario hybrid search exists for: a document
    that's an exact lexical match AND a decent semantic match should outrank
    a document that's only a semantic-adjacent match, which should in turn
    outrank a document that's neither."""
    from substrate.kernel.storage.vector import Document

    store = await _hybrid_test_store()
    if store is None:
        pytest.skip("Postgres not reachable")
    collection = f"hybrid-test-{id(object())}"
    query_vec = _vec384((0, 1.0))
    both = Document.from_text(
        "quarterly revenue grew 20 percent", embedding=_vec384((0, 0.95), (1, 0.05))
    )
    semantic_only = Document.from_text(
        "sales figures for the last quarter", embedding=_vec384((0, 0.9), (1, 0.1))
    )
    irrelevant = Document.from_text(
        "the weather was sunny in San Francisco", embedding=_vec384((2, 1.0))
    )
    await store.add([both, semantic_only, irrelevant], collection=collection)

    # dense_k=2 caps the dense candidate list to the two closest embeddings
    # (both, semantic_only) — irrelevant's orthogonal embedding falls out of
    # it, and it has no lexical match either, so it must be fully excluded.
    results = await store.hybrid_search(
        query_vec, "revenue", collection=collection, dense_k=2, lexical_k=10, fused_k=10
    )
    ids = [r.id for r in results]

    assert ids.index(both.id) < ids.index(semantic_only.id)
    assert irrelevant.id not in ids

    await store.delete_collection(collection)


async def test_pg_vector_store_hybrid_search_applies_filter() -> None:
    from substrate.kernel.storage.vector import Document

    store = await _hybrid_test_store()
    if store is None:
        pytest.skip("Postgres not reachable")
    collection = f"hybrid-filter-test-{id(object())}"
    keep = Document.from_text(
        "revenue report", embedding=_vec384((0, 1.0)), metadata={"file_id": "keep"}
    )
    drop = Document.from_text(
        "revenue report", embedding=_vec384((0, 1.0)), metadata={"file_id": "drop"}
    )
    await store.add([keep, drop], collection=collection)

    results = await store.hybrid_search(
        _vec384((0, 1.0)), "revenue", collection=collection, filter={"file_id": "keep"}
    )

    assert [r.id for r in results] == [keep.id]

    await store.delete_collection(collection)
