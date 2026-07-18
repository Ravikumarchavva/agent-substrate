"""Example 2-1: Memory Backends — raw history operations across all three storage tiers.

DurableHistoryProvider (Postgres) is the real default — conversation history
that survives a restart. RedisHistoryProvider is a faster, TTL'd cache in
front of that same durable store, not an alternative to it.
InMemoryHistoryProvider only exists for tests and local scratch runs; it's
the one non-durable exception, which is why its name says so.

Demonstrates using:
  - InMemoryHistoryProvider (non-durable — tests / local dev only)
  - RedisHistoryProvider (TTL'd hot-path cache, backed by the durable store)
  - DurableHistoryProvider (Postgres — the default, durable source of truth)
"""

from __future__ import annotations

import asyncio
import os

from substrate.agents.context import InMemoryHistoryProvider
from substrate.capabilities.history import RedisHistoryProvider, DurableHistoryProvider
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.core.identity import AgentId

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb",
)


async def main() -> None:
    agent_id = AgentId(type="helper", key="session-a")
    session_id = "demo-session"
    run_id = "run-123"

    # 1. InMemoryHistoryProvider — non-durable, no infra needed. The exception,
    # not the default: use this only for tests and local scratch runs.
    print("=== 1. InMemoryHistoryProvider (non-durable, testing only) ===")
    mem = InMemoryHistoryProvider()
    await mem.append(
        agent_id,
        ChatMessage(role=Role.USER, content=[TextBlock(text="What is the capital of France?")]),
        session_id=session_id,
        run_id=run_id,
    )
    await mem.append(
        agent_id,
        ChatMessage(role=Role.ASSISTANT, content=[TextBlock(text="Paris.")]),
        session_id=session_id,
        run_id=run_id,
    )

    msgs = await mem.get_messages(agent_id, session_id=session_id)
    print(f"  Messages stored: {len(msgs)}")
    for m in msgs:
        print(f"    [{m.role}]: {[b.text for b in m.content if isinstance(b, TextBlock)]}")

    # 2. RedisHistoryProvider — TTL'd cache in front of the durable store,
    # for hot-path reads. Not durable on its own; entries expire.
    print("\n=== 2. RedisHistoryProvider (TTL cache, requires Redis) ===")
    try:
        redis_provider = RedisHistoryProvider(redis_url=REDIS_URL, ttl=3600)
        await redis_provider.connect()
        await redis_provider.append(
            agent_id,
            ChatMessage(role=Role.USER, content=[TextBlock(text="Hello from Redis!")]),
            session_id=session_id,
            run_id=run_id,
        )
        await redis_provider.append(
            agent_id,
            ChatMessage(role=Role.ASSISTANT, content=[TextBlock(text="Hello back!")]),
            session_id=session_id,
            run_id=run_id,
        )

        redis_msgs = await redis_provider.get_messages(agent_id, session_id=session_id)
        print(f"  Messages in Redis: {len(redis_msgs)}")
        for m in redis_msgs:
            print(f"    [{m.role}]: {[b.text for b in m.content if isinstance(b, TextBlock)]}")

        await redis_provider.clear(agent_id, session_id=session_id)
        print("  Redis session cleared.")
        await redis_provider.disconnect()
    except Exception as exc:
        print(f"  [SKIP] Redis unavailable: {exc}")

    # 3. DurableHistoryProvider — the default in production. Postgres-backed,
    # survives a restart; this is the source of truth the Redis cache reads through.
    print("\n=== 3. DurableHistoryProvider (durable default, requires PostgreSQL) ===")
    try:
        pg_provider = DurableHistoryProvider(database_url=DB_URL)
        await pg_provider.connect()
        await pg_provider.append(
            agent_id,
            ChatMessage(role=Role.USER, content=[TextBlock(text="Hello from Postgres!")]),
            session_id=session_id,
            run_id=run_id,
        )
        await pg_provider.append(
            agent_id,
            ChatMessage(role=Role.ASSISTANT, content=[TextBlock(text="Hello back!")]),
            session_id=session_id,
            run_id=run_id,
        )

        pg_msgs = await pg_provider.get_messages(agent_id, session_id=session_id)
        print(f"  Messages in Postgres: {len(pg_msgs)}")
        for m in pg_msgs:
            print(f"    [{m.role}]: {[b.text for b in m.content if isinstance(b, TextBlock)]}")

        await pg_provider.clear(agent_id, session_id=session_id)
        print("  Postgres session cleared.")
        await pg_provider.disconnect()
    except Exception as exc:
        print(f"  [SKIP] Postgres unavailable: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
