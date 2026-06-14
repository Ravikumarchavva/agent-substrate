"""Factory for building a durable Postgres/Redis-backed Runtime.

Lives in ``capabilities`` (L2) because it instantiates the L2 durable backends
and injects them into the L1 ``Runtime``.  This keeps the dependency direction
correct — ``capabilities`` may import ``agents``, but ``agents`` must never
import ``capabilities``.

Usage::

    from ravi.capabilities.runtime import build_postgres_runtime

    async with build_postgres_runtime(
        postgres_url="postgresql://postgres:postgres@localhost:5432/agentdb",
        redis_url="redis://localhost:6379/0",
    ) as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, msg)

The asyncpg pool is created and closed by this factory; callers only manage the
``async with`` scope.  When ``redis_url`` is omitted the Journal falls back to
the in-process ``InMemoryJournal`` (effect-replay dedup is then process-local).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from ravi.agents.runtime import Runtime
from ravi.agents.runtime.backends import InMemoryJournal

from ravi.capabilities.runtime.pg_event_log import PostgresEventLog
from ravi.capabilities.runtime.pg_inbox import PostgresInbox
from ravi.capabilities.runtime.pg_scheduler import PostgresScheduler
from ravi.capabilities.runtime.redis_journal import RedisJournal


@asynccontextmanager
async def build_postgres_runtime(
    *,
    postgres_url: str,
    redis_url: str | None = None,
    journal_ttl_seconds: int = 86400,
) -> AsyncIterator[Runtime]:
    """Create a Runtime backed by Postgres (EventLog/Inbox/Scheduler) + Redis Journal.

    Postgres tables are created on entry (``IF NOT EXISTS``).  The asyncpg pool
    and Redis client are owned by this context manager and closed on exit.
    """
    import asyncpg

    pool = await asyncpg.create_pool(postgres_url)
    redis_client = None
    try:
        event_log = PostgresEventLog(pool)
        inbox = PostgresInbox(pool)
        scheduler = PostgresScheduler(pool)
        await event_log.setup()
        await inbox.setup()
        await scheduler.setup()

        if redis_url is not None:
            import redis.asyncio as aioredis

            redis_client = aioredis.from_url(redis_url)
            journal: object = RedisJournal(redis_client, ttl_seconds=journal_ttl_seconds)
        else:
            journal = InMemoryJournal()

        async with Runtime(
            event_log=event_log,
            inbox=inbox,
            scheduler=scheduler,
            journal=journal,
        ) as rt:
            yield rt
    finally:
        if redis_client is not None:
            await redis_client.aclose()
        await pool.close()


__all__ = ["build_postgres_runtime"]
