"""End-to-end integration tests for build_postgres_runtime().

Requires running Postgres (and optionally Redis).  Skips automatically when
the infra is not reachable.

Run with infra up:
    make infra-up
    uv run pytest tests/agents/test_runtime_postgres.py -v
"""

from __future__ import annotations

import asyncio
import os

import pytest

from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import DataPayload, Message
from ravi.kernel.runtime.communication import AskOutcome

_PG_URL = os.environ.get(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/agentdb"
).replace("+asyncpg", "")
_REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


async def _pg_reachable() -> bool:
    try:
        import asyncpg

        pool = await asyncpg.create_pool(_PG_URL, min_size=1, max_size=1)
        await pool.close()
        return True
    except Exception:
        return False


async def _redis_reachable() -> bool:
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(_REDIS_URL)
        await client.ping()
        await client.aclose()
        return True
    except Exception:
        return False


@pytest.fixture()
async def pg_runtime():
    """Runtime backed by Postgres only (in-memory journal)."""
    if not await _pg_reachable():
        pytest.skip("Postgres not reachable")
    from ravi.infrastructure.runtime import build_postgres_runtime

    async with build_postgres_runtime(postgres_url=_PG_URL) as rt:
        yield rt


@pytest.fixture()
async def pg_redis_runtime():
    """Runtime backed by Postgres + Redis."""
    if not await _pg_reachable():
        pytest.skip("Postgres not reachable")
    if not await _redis_reachable():
        pytest.skip("Redis not reachable")
    from ravi.infrastructure.runtime import build_postgres_runtime

    async with build_postgres_runtime(
        postgres_url=_PG_URL,
        redis_url=_REDIS_URL,
        journal_ttl_seconds=60,
    ) as rt:
        yield rt


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_id(name: str) -> AgentId:
    return AgentId(type=name, key=f"pg-test-{id(object())}")


def _msg(target: AgentId, data: dict | None = None) -> Message:
    return Message(target=target, payload=DataPayload(data=data or {}))


# ---------------------------------------------------------------------------
# Shared agent fixtures
# ---------------------------------------------------------------------------


class RecorderAgent:
    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id
        self.received: list[Message] = []
        self.done = asyncio.Event()

    async def run(self, ctx: object, inbox: list[Message]) -> None:
        self.received.extend(inbox)
        self.done.set()


class EchoAgent:
    def __init__(self, agent_id: AgentId) -> None:
        self.id = agent_id

    async def run(self, ctx: object, inbox: list[Message]) -> None:

        ctx = ctx  # type: ignore[assignment]
        for msg in inbox:
            if msg.reply_to is not None:
                data = (
                    msg.payload.data  # type: ignore[union-attr]
                    if isinstance(msg.payload, DataPayload)
                    else {}
                )
                await ctx.reply(msg, {"echo": data})  # type: ignore[attr-defined]


class AskerAgent:
    def __init__(self, agent_id: AgentId, target: AgentId) -> None:
        self.id = agent_id
        self.target = target
        self.outcome: AskOutcome | None = None
        self.done = asyncio.Event()

    async def run(self, ctx: object, inbox: list[Message]) -> None:

        ctx = ctx  # type: ignore[assignment]
        msg = _msg(self.target, {"value": 99})
        self.outcome = await ctx.ask(  # type: ignore[attr-defined]
            self.target, msg, timeout=5.0
        )
        self.done.set()


# ---------------------------------------------------------------------------
# 1. Fire-and-forget with Postgres backend
# ---------------------------------------------------------------------------


async def test_pg_fire_and_forget(pg_runtime) -> None:
    agent_id = _agent_id("recorder")
    agent = RecorderAgent(agent_id)

    await pg_runtime.register(agent)
    await pg_runtime.submit(agent_id, _msg(agent_id, {"hello": "postgres"}))
    await asyncio.wait_for(agent.done.wait(), timeout=5.0)

    payloads = [
        m.payload.data  # type: ignore[union-attr]
        for m in agent.received
        if isinstance(m.payload, DataPayload)
    ]
    assert {"hello": "postgres"} in payloads


# ---------------------------------------------------------------------------
# 2. Multiple messages delivered before agent starts
# ---------------------------------------------------------------------------


async def test_pg_batched_delivery(pg_runtime) -> None:
    """Multiple messages enqueued before the worker drains should all arrive."""
    agent_id = _agent_id("batcher")
    received: list[dict] = []
    barrier = asyncio.Event()

    class BatchAgent:
        id = agent_id

        async def run(self, ctx: object, inbox: list[Message]) -> None:
            for msg in inbox:
                if isinstance(msg.payload, DataPayload):
                    received.append(msg.payload.data)
            barrier.set()

    await pg_runtime.register(BatchAgent())
    for i in range(3):
        await pg_runtime.submit(agent_id, _msg(agent_id, {"seq": i}))

    await asyncio.wait_for(barrier.wait(), timeout=5.0)
    seqs = {d["seq"] for d in received}
    assert {0, 1, 2}.issubset(seqs)


# ---------------------------------------------------------------------------
# 3. Ask / reply round-trip with Postgres backend
# ---------------------------------------------------------------------------


async def test_pg_ask_reply(pg_runtime) -> None:
    echo_id = _agent_id("echo")
    asker_id = _agent_id("asker")
    echo = EchoAgent(echo_id)
    asker = AskerAgent(asker_id, echo_id)

    await pg_runtime.register(echo)
    await pg_runtime.register(asker)
    await pg_runtime.submit(asker_id, _msg(asker_id))

    await asyncio.wait_for(asker.done.wait(), timeout=8.0)
    assert asker.outcome is not None
    assert asker.outcome.kind == "replied"
    assert asker.outcome.result is not None
    assert asker.outcome.result.output.data == {"echo": {"value": 99}}


# ---------------------------------------------------------------------------
# 4. Fire-and-forget with Postgres + Redis (full Stage 1 stack)
# ---------------------------------------------------------------------------


async def test_pg_redis_fire_and_forget(pg_redis_runtime) -> None:
    agent_id = _agent_id("recorder-redis")
    agent = RecorderAgent(agent_id)

    await pg_redis_runtime.register(agent)
    await pg_redis_runtime.submit(agent_id, _msg(agent_id, {"hello": "redis-journal"}))
    await asyncio.wait_for(agent.done.wait(), timeout=5.0)

    payloads = [
        m.payload.data  # type: ignore[union-attr]
        for m in agent.received
        if isinstance(m.payload, DataPayload)
    ]
    assert {"hello": "redis-journal"} in payloads
