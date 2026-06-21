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

``tail`` is event-driven via Postgres LISTEN/NOTIFY: ``append`` fires
``pg_notify`` and a single shared listener connection wakes the per-run waiters
(so concurrent tails don't each pin a pool connection).  A slow poll runs as a
safety backstop in case a notification is ever missed.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, AsyncIterator

from substrate.kernel.core.content import JsonObject
from substrate.kernel.core.errors import ConcurrentAppendError
from substrate.kernel.runtime.ids import RunId
from substrate.kernel.runtime.log_entry import RunLogEntry

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

# Safety backstop: even with LISTEN/NOTIFY, re-poll this often in case a
# notification is ever dropped (e.g. listener connection blip).
_TAIL_FALLBACK_INTERVAL = 2.0  # seconds
_CHANNEL = "ravi_evlog"  # NOTIFY channel; payload is the run_id


class PostgresEventLog:
    """Postgres-backed append-only EventLog implementing the kernel EventLog Protocol."""

    def __init__(self, pool: asyncpg.Pool, *, dsn: str | None = None) -> None:
        self._pool = pool
        # LISTEN needs a long-lived connection. Use a *dedicated* one (not a
        # pool connection) so it never reduces pool capacity or blocks
        # pool.close().  Without a dsn the listener is disabled and tail() falls
        # back to the safety poll — correct, just higher latency.
        self._dsn = dsn
        self._listen_conn: asyncpg.Connection | None = None
        self._listen_lock = asyncio.Lock()
        self._waiters: dict[str, set[asyncio.Event]] = {}

    async def setup(self) -> None:
        """Create the table if it does not exist."""
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_TABLE)

    # -- LISTEN/NOTIFY plumbing ---------------------------------------------

    async def _ensure_listener(self) -> None:
        """Lazily hold one dedicated connection LISTENing on the notify channel.

        Best-effort: if it fails, ``tail`` still makes progress via the poll
        backstop, so callers never block on listener setup.
        """
        if self._listen_conn is not None or self._dsn is None:
            return
        async with self._listen_lock:
            if self._listen_conn is not None:
                return
            try:
                import asyncpg

                conn = await asyncpg.connect(self._dsn)
                await conn.add_listener(_CHANNEL, self._on_notify)
                self._listen_conn = conn
            except Exception:  # pragma: no cover - listener is best-effort
                pass

    def _on_notify(self, _conn: object, _pid: int, _channel: str, payload: str) -> None:
        """asyncpg callback (same loop): wake every tailer for this run_id."""
        for ev in self._waiters.get(payload, ()):
            ev.set()

    async def close(self) -> None:
        """Close the dedicated listener connection (call on teardown)."""
        if self._listen_conn is not None:
            try:
                await self._listen_conn.close()
            except Exception:  # pragma: no cover
                pass
            self._listen_conn = None

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
                await conn.execute("SELECT pg_advisory_xact_lock($1)", lock_key)
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
                # Wake any tailers once the row is committed (NOTIFY is
                # transactional — delivered on COMMIT).
                await conn.execute("SELECT pg_notify($1, $2)", _CHANNEL, run_id)
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
        await self._ensure_listener()
        waker = asyncio.Event()
        self._waiters.setdefault(run_id, set()).add(waker)
        try:
            idx = from_seq
            while True:
                # Clear before reading so a NOTIFY landing during/after the read
                # is never lost — the next wait returns immediately.
                waker.clear()
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
                if rows:
                    for row in rows:
                        yield _row_to_entry(run_id, row)
                        idx = row["seq"] + 1
                    continue  # drain any remaining rows before waiting
                try:
                    await asyncio.wait_for(
                        waker.wait(), timeout=_TAIL_FALLBACK_INTERVAL
                    )
                except asyncio.TimeoutError:
                    pass  # backstop poll
        finally:
            waiters = self._waiters.get(run_id)
            if waiters is not None:
                waiters.discard(waker)
                if not waiters:
                    self._waiters.pop(run_id, None)

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
    payload: JsonObject = (
        (json.loads(raw) if isinstance(raw, str) else dict(raw)) if raw else {}
    )
    return RunLogEntry(
        run_id=run_id,
        seq=row["seq"],  # type: ignore[index]
        kind=row["kind"],  # type: ignore[index]
        payload=payload,
        ts=ts_val,
    )


__all__ = ["PostgresEventLog"]
