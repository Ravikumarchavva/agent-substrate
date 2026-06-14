"""capabilities.runtime — Stage 1 durable backends for the agent runtime.

These implementations replace the Stage 0 in-memory backends when
``Runtime(backend="postgres", ...)`` is used.  They implement the same
kernel Protocols (EventLog, Inbox, Journal, Scheduler), so no agent code
needs to change when switching backends.

Usage is through the Runtime factory::

    async with Runtime(
        backend="postgres",
        postgres_url="postgresql://postgres:postgres@localhost:5432/agentdb",
        redis_url="redis://localhost:6379/0",
    ) as rt:
        await rt.register(agent)
        run_id = await rt.submit(agent.id, msg)
"""

from __future__ import annotations

from ravi.capabilities.runtime.pg_event_log import PostgresEventLog
from ravi.capabilities.runtime.pg_inbox import PostgresInbox
from ravi.capabilities.runtime.pg_scheduler import PostgresScheduler
from ravi.capabilities.runtime.redis_journal import RedisJournal

__all__ = [
    "PostgresEventLog",
    "PostgresInbox",
    "PostgresScheduler",
    "RedisJournal",
]
