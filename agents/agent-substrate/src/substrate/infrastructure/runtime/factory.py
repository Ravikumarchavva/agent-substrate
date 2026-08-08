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

Effect-result durability (LLM/tool call dedup) is provided by the EventLogProtocol
itself (``effect.result`` entries, folded into an ``EffectCache`` per lease —
see ``agents/runtime/effect_cache.py``), not by a separate Redis journal.
This removed the one durability gap a TTL'd store had: a run suspended or
orphaned longer than the TTL used to come back to a journal miss on every
effect (LLM calls re-billed, tools re-executed) — the EventLogProtocol never expires.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from substrate.agents.runtime import Runtime
from substrate.logger import setup_logging

from substrate.infrastructure.runtime.event_log import EventLog
from substrate.infrastructure.runtime.inbox import Inbox
from substrate.infrastructure.runtime.scheduler import Scheduler
from substrate.infrastructure.runtime.signal_bus import SignalBus
from substrate.infrastructure.runtime.supervisor import Supervisor

logger = setup_logging()


@asynccontextmanager
async def build_postgres_runtime(
    *,
    postgres_url: str,
    reclaim_orphans: bool = False,
    pool_min_size: int = 2,
    pool_max_size: int = 10,
) -> AsyncIterator[Runtime]:
    """Create a Runtime backed by Postgres (EventLogProtocol/InboxProtocol/SchedulerProtocol/SignalBusProtocol).

    Postgres tables are created on entry (``IF NOT EXISTS``).  The asyncpg
    pool is owned by this context manager and closed on exit.

    ``reclaim_orphans=True`` runs one immediate expired-lease sweep
    (``Scheduler.reclaim_orphans()``) before ``resume_pending_runs()`` reads
    ``pending`` rows to rebuild agents — without it, a run genuinely orphaned
    by a crashed previous process wouldn't show up as ``pending`` until its
    lease naturally expires and a live ``Worker`` polls again, which could
    leave cold-resume seeing nothing to rebuild for up to the full lease TTL.
    Safe for single- or multi-worker deployments alike: it only ever reclaims
    a lease whose ``expires_at`` has already passed, so it can never steal a
    still-live process's run (see that method's docstring for why "single
    worker" used to be treated as license to skip the expiry check, and the
    race that caused).

    ``pool_min_size``/``pool_max_size`` bound this runtime's OWN asyncpg pool —
    separate from any ORM engine pool (e.g. the monolith's SQLAlchemy Thread/
    Feedback engine) sharing the same Postgres instance. Size against your
    Postgres ``max_connections`` accordingly: this pool's max, plus the ORM
    pool's max, times replica count.
    """
    import asyncpg

    pool = await asyncpg.create_pool(
        postgres_url, min_size=pool_min_size, max_size=pool_max_size
    )
    event_log: EventLog | None = None
    try:
        event_log = EventLog(pool, dsn=postgres_url)
        inbox = Inbox(pool)
        scheduler = Scheduler(pool)
        signal_bus = SignalBus(pool)
        supervisor = Supervisor(
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
            n = await scheduler.reclaim_orphans()
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
        if event_log is not None:
            await event_log.close()
        await pool.close()


__all__ = ["build_postgres_runtime"]
