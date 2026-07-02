"""Retention sweep — delete durable runtime state for old terminal runs.

Not wired into any automatic poll loop — call this from an ops cron job or a
one-off maintenance script. A run's full history (EventLog, spawn tree,
signals) has no automatic expiry otherwise, so left unswept these tables grow
without bound for the lifetime of the deployment.

Only ``ravi_run_queue`` rows with ``terminated_at`` older than the cutoff are
candidates — a row with ``terminated_at IS NULL`` is still live (pending,
running, or suspended) and is never touched, no matter how old
``enqueued_at`` is.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg


async def sweep_terminal_runs(pool: asyncpg.Pool, *, older_than: timedelta) -> int:
    """Delete all durable state for runs terminal for longer than ``older_than``.

    Deletes, per candidate run_id, across every table keyed by run_id:
    ``ravi_event_log``, ``ravi_signals``, ``ravi_spawn_effects`` (keyed by
    effect_id, not run_id — swept via the child_run_id it maps to),
    ``ravi_run_tree``, ``ravi_agent_runs``, and finally ``ravi_run_queue``
    itself. Deliberately does NOT touch ``ravi_inbox`` — it's keyed by
    ``agent_id``, not ``run_id`` (one agent can have many runs over its
    lifetime, and its inbox is a shared queue across all of them, drained by
    whichever run is currently active), so there is no safe way to scope an
    inbox deletion to a single terminal run without risking a live message
    meant for that agent's next run.

    Returns the number of runs swept.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                """
                SELECT run_id FROM ravi_run_queue
                WHERE terminated_at IS NOT NULL AND terminated_at < now() - $1::interval
                """,
                older_than,
            )
            run_ids = [row["run_id"] for row in rows]
            if not run_ids:
                return 0

            await conn.execute(
                "DELETE FROM ravi_event_log WHERE run_id = ANY($1)", run_ids
            )
            await conn.execute(
                "DELETE FROM ravi_signals WHERE run_id = ANY($1)", run_ids
            )
            await conn.execute(
                "DELETE FROM ravi_spawn_effects WHERE child_run_id = ANY($1)", run_ids
            )
            await conn.execute(
                "DELETE FROM ravi_run_tree WHERE run_id = ANY($1)", run_ids
            )
            await conn.execute(
                "DELETE FROM ravi_agent_runs WHERE run_id = ANY($1)", run_ids
            )
            await conn.execute(
                "DELETE FROM ravi_run_queue WHERE run_id = ANY($1)", run_ids
            )
    return len(run_ids)


__all__ = ["sweep_terminal_runs"]
