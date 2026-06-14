"""PostgresEventLog — Stage 1 durable EventLog backed by asyncpg.

Schema (created on setup())::

    CREATE TABLE ravi_event_log (
        run_id  TEXT        NOT NULL,
        seq     INTEGER     NOT NULL,
        kind    TEXT        NOT NULL,
        payload JSONB       NOT NULL DEFAULT '{}',
        ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (run_id, seq)
    );

Optimistic concurrency: ``append`` takes an advisory lock on hash(run_id)
before checking MAX(seq), so two workers racing on the same run always
serialise rather than clobber each other.

``tail`` polls with a short sleep; use Stage 2 (LISTEN/NOTIFY or NATS) for
sub-second latency.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncIterator

from ravi.kernel.core.content import JsonObject
from ravi.kernel.core.errors import ConcurrentAppendError
from ravi.kernel.runtime.ids import RunId
from ravi.kernel.runtime.log_entry import RunLogEntry

if TYPE_CHECKING:
    import asyncpg

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS ravi_event_log (
    run_id  TEXT        NOT NULL,
    seq     INTEGER     NOT NULL,
    kind    TEXT        NOT NULL,
    payload JSONB       NOT NULL DEFAULT '{}',
    ts      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (run_id, seq)
);
CREATE INDEX IF NOT EXISTS ravi_event_log_run_seq
    ON ravi_event_log (run_id, seq);
"""

_TAIL_POLL_INTERVAL = 0.1  # seconds


class PostgresEventLog:
    """Postgres-backed append-only EventLog implementing the kernel EventLog Protocol."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        """Create the table if it does not exist."""
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_TABLE)

    async def append(
        self,
        run_id: RunId,
        entry: RunLogEntry,
        *,
        expected_seq: int,
    ) -> int:
        lock_key = _lock_key(run_id)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "SELECT pg_advisory_xact_lock($1)", lock_key
                )
                current: int = await conn.fetchval(
                    "SELECT COALESCE(MAX(seq), -1) FROM ravi_event_log WHERE run_id = $1",
                    run_id,
                )
                if current != expected_seq:
                    raise ConcurrentAppendError(
                        f"expected seq {expected_seq}, got {current}",
                        run_id=run_id,
                        expected_seq=expected_seq,
                        actual_seq=current,
                    )
                new_seq = current + 1
                await conn.execute(
                    """
                    INSERT INTO ravi_event_log (run_id, seq, kind, payload, ts)
                    VALUES ($1, $2, $3, $4::jsonb, $5)
                    """,
                    run_id,
                    new_seq,
                    entry.kind,
                    json.dumps(entry.payload),
                    entry.ts,
                )
                return new_seq

    def read(self, run_id: RunId, *, from_seq: int = 0) -> AsyncIterator[RunLogEntry]:
        return self._read_iter(run_id, from_seq)

    async def _read_iter(
        self, run_id: RunId, from_seq: int
    ) -> AsyncIterator[RunLogEntry]:  # type: ignore[return]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT seq, kind, payload, ts
                FROM ravi_event_log
                WHERE run_id = $1 AND seq >= $2
                ORDER BY seq
                """,
                run_id,
                from_seq,
            )
        for row in rows:
            yield _row_to_entry(run_id, row)

    def tail(self, run_id: RunId, *, from_seq: int = 0) -> AsyncIterator[RunLogEntry]:
        return self._tail_iter(run_id, from_seq)

    async def _tail_iter(
        self, run_id: RunId, from_seq: int
    ) -> AsyncIterator[RunLogEntry]:  # type: ignore[return]
        idx = from_seq
        while True:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT seq, kind, payload, ts
                    FROM ravi_event_log
                    WHERE run_id = $1 AND seq >= $2
                    ORDER BY seq
                    LIMIT 100
                    """,
                    run_id,
                    idx,
                )
            for row in rows:
                yield _row_to_entry(run_id, row)
                idx = row["seq"] + 1
            if not rows:
                await asyncio.sleep(_TAIL_POLL_INTERVAL)

    async def last_seq(self, run_id: RunId) -> int:
        async with self._pool.acquire() as conn:
            result: int | None = await conn.fetchval(
                "SELECT MAX(seq) FROM ravi_event_log WHERE run_id = $1",
                run_id,
            )
        return result if result is not None else -1


def _lock_key(run_id: RunId) -> int:
    return hash(run_id) & 0x7FFFFFFFFFFFFFFF


def _row_to_entry(run_id: RunId, row: object) -> RunLogEntry:
    ts_val = row["ts"]  # type: ignore[index]
    if isinstance(ts_val, datetime) and ts_val.tzinfo is None:
        ts_val = ts_val.replace(tzinfo=timezone.utc)
    raw = row["payload"]  # type: ignore[index]
    payload: JsonObject = (json.loads(raw) if isinstance(raw, str) else dict(raw)) if raw else {}
    return RunLogEntry(
        run_id=run_id,
        seq=row["seq"],  # type: ignore[index]
        kind=row["kind"],  # type: ignore[index]
        payload=payload,
        ts=ts_val,
    )


__all__ = ["PostgresEventLog"]
