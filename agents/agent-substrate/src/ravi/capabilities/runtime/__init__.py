"""capabilities.runtime — durable Postgres/Redis backends for the agent runtime.

These implement the same kernel Protocols (EventLog, Inbox, Journal, Scheduler)
as the Stage 0 in-memory backends, so agent code never changes when switching
backends.  Use ``build_postgres_runtime`` to construct a Runtime wired to them
— the agents-layer ``Runtime`` never imports these classes directly, keeping
``agents`` strictly above ``capabilities``.

Usage::

    from ravi.capabilities.runtime import build_postgres_runtime

    async with build_postgres_runtime(
        postgres_url="postgresql://postgres:postgres@localhost:5432/agentdb",
        redis_url="redis://localhost:6379/0",
    ) as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, msg)
"""

from __future__ import annotations

from ravi.capabilities.runtime.factory import build_postgres_runtime
from ravi.capabilities.runtime.pg_event_log import PostgresEventLog
from ravi.capabilities.runtime.pg_inbox import PostgresInbox
from ravi.capabilities.runtime.pg_scheduler import PostgresScheduler
from ravi.capabilities.runtime.redis_journal import RedisJournal

__all__ = [
    "build_postgres_runtime",
    "PostgresEventLog",
    "PostgresInbox",
    "PostgresScheduler",
    "RedisJournal",
]
