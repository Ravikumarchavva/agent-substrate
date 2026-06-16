"""Factory for building a durable Postgres/Redis-backed Runtime.

Usage::

    from ravi.infrastructure.runtime import build_postgres_runtime

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
from ravi.logger import setup_logging

from ravi.infrastructure.runtime.pg_event_log import PostgresEventLog
from ravi.infrastructure.runtime.pg_inbox import PostgresInbox
from ravi.infrastructure.runtime.pg_scheduler import PostgresScheduler
from ravi.infrastructure.runtime.redis_journal import RedisJournal

logger = setup_logging()


@asynccontextmanager
async def build_postgres_runtime(
    *,
    postgres_url: str,
    redis_url: str | None = None,
    journal_ttl_seconds: int = 86400,
    reclaim_orphans: bool = False,
) -> AsyncIterator[Runtime]:
    """Create a Runtime backed by Postgres (EventLog/Inbox/Scheduler) + Redis Journal.

    Postgres tables are created on entry (``IF NOT EXISTS``).  The asyncpg pool
    and Redis client are owned by this context manager and closed on exit.

    ``reclaim_orphans=True`` is for **single-worker** deployments (e.g. the
    monolith): on startup every ``running`` row is necessarily orphaned by the
    previous process, so it is requeued immediately instead of waiting out the
    lease.  Leave it ``False`` for multi-replica workers (lease expiry handles
    orphans safely there).
    """
    import asyncpg

    pool = await asyncpg.create_pool(postgres_url)
    redis_client = None
    try:
        event_log = PostgresEventLog(pool, dsn=postgres_url)
        inbox = PostgresInbox(pool)
        scheduler = PostgresScheduler(pool)
        await event_log.setup()
        await inbox.setup()
        await scheduler.setup()
        if reclaim_orphans:
            n = await scheduler.reclaim_orphans(all_running=True)
            if n:
                logger.info("Reclaimed %d orphaned run(s) from previous process", n)

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
        await event_log.close()
        if redis_client is not None:
            await redis_client.aclose()
        await pool.close()


__all__ = ["build_postgres_runtime"]
