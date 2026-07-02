"""Factory for building a durable Postgres-backed Runtime.

Usage::

    from substrate.infrastructure.runtime import build_postgres_runtime

    async with build_postgres_runtime(
        postgres_url="postgresql://postgres:postgres@localhost:5432/agentdb",
    ) as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, msg)

The asyncpg pool is created and closed by this factory; callers only manage
the ``async with`` scope.

Effect-result durability (LLM/tool call dedup) is provided by the EventLog
itself (``effect.result`` entries, folded into an ``EffectCache`` per lease —
see ``agents/runtime/effect_cache.py``), not by a separate Redis journal.
This removed the one durability gap a TTL'd store had: a run suspended or
orphaned longer than the TTL used to come back to a journal miss on every
effect (LLM calls re-billed, tools re-executed) — the EventLog never expires.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from substrate.agents.runtime import Runtime
from substrate.logger import setup_logging

from substrate.infrastructure.runtime.pg_event_log import PostgresEventLog
from substrate.infrastructure.runtime.pg_inbox import PostgresInbox
from substrate.infrastructure.runtime.pg_scheduler import PostgresScheduler
from substrate.infrastructure.runtime.pg_signal_bus import PostgresSignalBus
from substrate.infrastructure.runtime.pg_supervisor import PostgresSupervisor

logger = setup_logging()


@asynccontextmanager
async def build_postgres_runtime(
    *,
    postgres_url: str,
    reclaim_orphans: bool = False,
) -> AsyncIterator[Runtime]:
    """Create a Runtime backed by Postgres (EventLog/Inbox/Scheduler/SignalBus).

    Postgres tables are created on entry (``IF NOT EXISTS``).  The asyncpg
    pool is owned by this context manager and closed on exit.

    ``reclaim_orphans=True`` is for **single-worker** deployments (e.g. the
    monolith): on startup every ``running`` row is necessarily orphaned by the
    previous process, so it is requeued immediately instead of waiting out the
    lease.  Leave it ``False`` for multi-replica workers (lease expiry handles
    orphans safely there).
    """
    import asyncpg

    pool = await asyncpg.create_pool(postgres_url)
    try:
        event_log = PostgresEventLog(pool, dsn=postgres_url)
        inbox = PostgresInbox(pool)
        scheduler = PostgresScheduler(pool)
        signal_bus = PostgresSignalBus(pool)
        supervisor = PostgresSupervisor(
            pool,
            event_log=event_log,
            inbox=inbox,
            scheduler=scheduler,
            signal_bus=signal_bus,
        )
        await event_log.setup()
        await inbox.setup()
        await signal_bus.setup()
        await scheduler.setup()
        await supervisor.setup()
        if reclaim_orphans:
            n = await scheduler.reclaim_orphans(all_running=True)
            if n:
                logger.info("Reclaimed %d orphaned run(s) from previous process", n)

        async with Runtime(
            event_log=event_log,
            inbox=inbox,
            scheduler=scheduler,
            signal_bus=signal_bus,
            supervisor=supervisor,
        ) as rt:
            yield rt
    finally:
        await event_log.close()
        await pool.close()


__all__ = ["build_postgres_runtime"]
