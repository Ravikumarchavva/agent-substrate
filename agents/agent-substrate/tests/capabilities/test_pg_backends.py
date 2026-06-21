"""Integration tests for Stage 1 Postgres + Redis backends.

Requires running Postgres and (for RedisJournal) Redis.
Skip automatically when DATABASE_URL / REDIS_URL are not reachable.

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
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


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


@pytest.fixture()
async def redis_client():
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(_REDIS_URL)
        await client.ping()
        yield client
        await client.aclose()
    except Exception:
        pytest.skip("Redis not reachable")


# ---------------------------------------------------------------------------
# PostgresEventLog
# ---------------------------------------------------------------------------


async def test_pg_event_log_append_and_read(pg_pool) -> None:
    from substrate.infrastructure.runtime import PostgresEventLog
    from substrate.kernel.runtime.log_entry import RunLogEntry
    from substrate.kernel.runtime.ids import new_run_id

    log = PostgresEventLog(pg_pool)
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
    from substrate.infrastructure.runtime import PostgresEventLog
    from substrate.kernel.runtime.log_entry import RunLogEntry
    from substrate.kernel.core.errors import ConcurrentAppendError
    from substrate.kernel.runtime.ids import new_run_id

    log = PostgresEventLog(pg_pool)
    await log.setup()

    run_id = new_run_id()
    e0 = RunLogEntry(run_id=run_id, seq=0, kind="run.started", payload={})
    await log.append(run_id, e0, expected_seq=-1)

    e1 = RunLogEntry(run_id=run_id, seq=1, kind="msg.received", payload={})
    with pytest.raises(ConcurrentAppendError):
        await log.append(run_id, e1, expected_seq=-1)  # wrong expected_seq


async def test_pg_event_log_last_seq(pg_pool) -> None:
    from substrate.infrastructure.runtime import PostgresEventLog
    from substrate.kernel.runtime.log_entry import RunLogEntry
    from substrate.kernel.runtime.ids import new_run_id

    log = PostgresEventLog(pg_pool)
    await log.setup()

    run_id = new_run_id()
    assert await log.last_seq(run_id) == -1

    e = RunLogEntry(run_id=run_id, seq=0, kind="run.started", payload={})
    await log.append(run_id, e, expected_seq=-1)
    assert await log.last_seq(run_id) == 0


async def test_pg_event_log_tail_yields_existing(pg_pool) -> None:
    from substrate.infrastructure.runtime import PostgresEventLog
    from substrate.kernel.runtime.log_entry import RunLogEntry
    from substrate.kernel.runtime.ids import new_run_id

    log = PostgresEventLog(pg_pool)
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
# PostgresInbox
# ---------------------------------------------------------------------------


async def test_pg_inbox_deliver_and_drain(pg_pool) -> None:
    from substrate.infrastructure.runtime import PostgresInbox
    from substrate.kernel.core.identity import AgentId
    from substrate.kernel.messaging.message import Message
    from substrate.kernel.core.content import TextBlock
    from substrate.kernel.core.content import ChatMessage, Role
    from substrate.kernel.messaging.message import ChatPayload

    inbox = PostgresInbox(pg_pool)
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
    from substrate.infrastructure.runtime import PostgresInbox
    from substrate.kernel.core.identity import AgentId
    from substrate.kernel.messaging.message import Message
    from substrate.kernel.core.content import TextBlock
    from substrate.kernel.core.content import ChatMessage, Role
    from substrate.kernel.messaging.message import ChatPayload

    inbox = PostgresInbox(pg_pool, max_retries=2)
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
# RedisJournal
# ---------------------------------------------------------------------------


async def test_redis_journal_lookup_miss(redis_client) -> None:
    from substrate.infrastructure.runtime import RedisJournal

    journal = RedisJournal(redis_client, ttl_seconds=10)
    result = await journal.lookup("nonexistent-effect-id")
    assert result is None


async def test_redis_journal_record_and_lookup(redis_client) -> None:
    from substrate.infrastructure.runtime import RedisJournal
    from substrate.kernel.runtime.effects import EffectResult

    journal = RedisJournal(redis_client, ttl_seconds=10)
    effect_id = f"test-effect-{id(object())}"
    result = EffectResult(effect_id=effect_id, status="ok", value={"x": 42})
    await journal.record(result)

    found = await journal.lookup(effect_id)
    assert found is not None
    assert found.status == "ok"
    assert found.value == {"x": 42}


async def test_redis_journal_at_most_once(redis_client) -> None:
    from substrate.infrastructure.runtime import RedisJournal
    from substrate.kernel.runtime.effects import EffectResult

    journal = RedisJournal(redis_client, ttl_seconds=10)
    effect_id = f"test-amo-{id(object())}"

    r1 = EffectResult(effect_id=effect_id, status="ok", value={"v": 1})
    r2 = EffectResult(effect_id=effect_id, status="ok", value={"v": 2})
    await journal.record(r1)
    await journal.record(r2)  # must be a no-op

    found = await journal.lookup(effect_id)
    assert found is not None
    assert found.value == {"v": 1}  # first writer wins


# ---------------------------------------------------------------------------
# PostgresScheduler
# ---------------------------------------------------------------------------


async def test_pg_scheduler_enqueue_and_lease(pg_pool) -> None:
    from substrate.infrastructure.runtime import PostgresScheduler
    from substrate.kernel.runtime.ids import new_run_id, RunStatus
    from substrate.kernel.core.identity import AgentId

    sched = PostgresScheduler(pg_pool)
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
    from substrate.infrastructure.runtime import PostgresScheduler
    from substrate.kernel.runtime.ids import new_run_id
    from substrate.kernel.core.identity import AgentId

    sched = PostgresScheduler(pg_pool)
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
    from substrate.infrastructure.runtime import PostgresScheduler
    from substrate.kernel.runtime.ids import new_run_id, RunStatus
    from substrate.kernel.core.identity import AgentId

    sched = PostgresScheduler(pg_pool)
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


async def test_pg_scheduler_find_run_for_agent(pg_pool) -> None:
    from substrate.infrastructure.runtime import PostgresScheduler
    from substrate.kernel.runtime.ids import new_run_id, RunStatus
    from substrate.kernel.core.identity import AgentId

    sched = PostgresScheduler(pg_pool)
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
