"""PostgresSignalBus — Stage 1 durable SignalBus backed by asyncpg.

Schema::

    CREATE TABLE substrate_signals (
        id          BIGSERIAL PRIMARY KEY,
        run_id      TEXT NOT NULL,
        name        TEXT NOT NULL,
        payload     JSONB NOT NULL DEFAULT '{}',
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
        consumed_at TIMESTAMPTZ,
        consumed_by TEXT
    );

Buffered, exactly-once-per-effect_id semantics — see the kernel ``SignalBus``
Protocol docstring for the guarantees. Coupled with ``PostgresScheduler`` by
design: ``signal()`` wakes a matching suspended run in the same transaction
as the buffer insert (``substrate_run_queue.wake_signals``), and
``PostgresScheduler.release(SUSPENDED)`` double-checks this table for an
already-arrived signal before actually parking — both sides of the
lost-wakeup race are closed by sharing one database, one transaction each.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING

from substrate.kernel.core.content import JsonObject
from substrate.kernel.runtime.ids import RunId

if TYPE_CHECKING:
    import asyncpg

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS substrate_signals (
    id          BIGSERIAL PRIMARY KEY,
    run_id      TEXT NOT NULL,
    name        TEXT NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at TIMESTAMPTZ,
    consumed_by TEXT
);
CREATE INDEX IF NOT EXISTS substrate_signals_pending_idx
    ON substrate_signals (run_id, name)
    WHERE consumed_at IS NULL;
CREATE INDEX IF NOT EXISTS substrate_signals_consumer_idx
    ON substrate_signals (consumed_by)
    WHERE consumed_by IS NOT NULL;
"""


class PostgresSignalBus:
    """Postgres-backed SignalBus implementing the kernel SignalBus Protocol."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_TABLE)

    async def signal(self, run_id: RunId, name: str, payload: JsonObject) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO substrate_signals (run_id, name, payload)
                    VALUES ($1, $2, $3::jsonb)
                    """,
                    run_id,
                    name,
                    json.dumps(payload),
                )
                # Wake the run only if it's suspended AND waiting on this
                # specific name — matches PostgresScheduler.release()'s
                # wake_signals column so an unrelated signal never causes a
                # pointless replay.
                await conn.execute(
                    """
                    UPDATE substrate_run_queue
                    SET status = 'pending', worker_id = NULL, expires_at = NULL
                    WHERE run_id = $1 AND status = 'suspended' AND $2 = ANY(wake_signals)
                    """,
                    run_id,
                    name,
                )
                await conn.execute("SELECT pg_notify('substrate_sched', $1)", run_id)

    async def consume(
        self, run_id: RunId, name: str, effect_id: str
    ) -> JsonObject | None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Idempotent re-claim: a replay of the same journaled wait
                # must get back the SAME payload it already consumed, not a
                # different (or absent) one.
                row = await conn.fetchrow(
                    "SELECT payload FROM substrate_signals WHERE consumed_by = $1",
                    effect_id,
                )
                if row is not None:
                    return _decode(row["payload"])

                row = await conn.fetchrow(
                    """
                    UPDATE substrate_signals
                    SET consumed_at = now(), consumed_by = $1
                    WHERE id = (
                        SELECT id FROM substrate_signals
                        WHERE run_id = $2 AND name = $3 AND consumed_at IS NULL
                        ORDER BY id
                        LIMIT 1
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING payload
                    """,
                    effect_id,
                    run_id,
                    name,
                )
                return _decode(row["payload"]) if row is not None else None

    async def timer(self, run_id: RunId, at: datetime) -> None:
        """Set ``wake_at`` for the run — no active timer/task.

        A DB row can't sleep; the scheduler's existing lease-poll cadence
        (every ``Worker.POLL_INTERVAL``) picks up any suspended row whose
        ``wake_at`` has passed. See ``PostgresScheduler.lease()``.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE substrate_run_queue SET wake_at = $1 WHERE run_id = $2",
                at,
                run_id,
            )


def _decode(raw: object) -> JsonObject:
    return json.loads(raw) if isinstance(raw, str) else dict(raw)  # type: ignore[arg-type]


__all__ = ["PostgresSignalBus"]
