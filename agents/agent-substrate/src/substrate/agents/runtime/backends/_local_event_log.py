"""LocalEventLog — SQLite-durable EventLogProtocol (no-infra tier).

Satisfies EventLogProtocol exactly, same as InMemoryEventLog — a caller
cannot tell which backend it's talking to. ``tail()`` polls rather than
blocking on an in-process asyncio.Event (there is no cross-process event
primitive over a SQLite file), which is the same tradeoff the Postgres tier
makes for its own ``tail()`` — see ``infrastructure/runtime/event_log.py``.
"""

from __future__ import annotations

import asyncio
import sqlite3
from typing import AsyncIterator

from substrate.kernel.exceptions import ConcurrentAppendError
from substrate.kernel.runtime.ids import RunId
from substrate.kernel.runtime.log_entry import RunLogEntry

from ._local_db import LocalRuntimeDB

_POLL_INTERVAL_S = 0.2


class LocalEventLog:
    """SQLite-backed, single-writer-serialized EventLogProtocol."""

    def __init__(self, db: LocalRuntimeDB) -> None:
        self._db = db

    async def append(
        self, run_id: RunId, entry: RunLogEntry, *, expected_seq: int
    ) -> int:
        def _do(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT MAX(seq) AS m FROM event_log WHERE run_id = ?", (run_id,)
            ).fetchone()
            current = row["m"] if row["m"] is not None else -1
            if current != expected_seq:
                raise ConcurrentAppendError(
                    f"expected seq {expected_seq}, got {current}",
                    run_id=run_id,
                    expected_seq=expected_seq,
                    actual_seq=current,
                )
            new_seq = current + 1
            conn.execute(
                "INSERT INTO event_log (run_id, seq, entry_json) VALUES (?, ?, ?)",
                (run_id, new_seq, entry.model_dump_json()),
            )
            return new_seq

        return await self._db.run(_do)

    def read(self, run_id: RunId, *, from_seq: int = 0) -> AsyncIterator[RunLogEntry]:
        return self._read_iter(run_id, from_seq)

    async def _read_iter(
        self, run_id: RunId, from_seq: int
    ) -> AsyncIterator[RunLogEntry]:  # type: ignore[return]
        for entry in await self._fetch_from(run_id, from_seq):
            yield entry

    def tail(self, run_id: RunId, *, from_seq: int = 0) -> AsyncIterator[RunLogEntry]:
        return self._tail_iter(run_id, from_seq)

    async def _tail_iter(
        self, run_id: RunId, from_seq: int
    ) -> AsyncIterator[RunLogEntry]:  # type: ignore[return]
        idx = from_seq
        while True:
            entries = await self._fetch_from(run_id, idx)
            for entry in entries:
                yield entry
                idx = entry.seq + 1
            if not entries:
                await asyncio.sleep(_POLL_INTERVAL_S)

    async def _fetch_from(self, run_id: RunId, from_seq: int) -> list[RunLogEntry]:
        def _do(conn: sqlite3.Connection) -> list[RunLogEntry]:
            rows = conn.execute(
                "SELECT entry_json FROM event_log "
                "WHERE run_id = ? AND seq >= ? ORDER BY seq",
                (run_id, from_seq),
            ).fetchall()
            return [RunLogEntry.model_validate_json(r["entry_json"]) for r in rows]

        return await self._db.run(_do)

    async def last_seq(self, run_id: RunId) -> int:
        def _do(conn: sqlite3.Connection) -> int:
            row = conn.execute(
                "SELECT MAX(seq) AS m FROM event_log WHERE run_id = ?", (run_id,)
            ).fetchone()
            return row["m"] if row["m"] is not None else -1

        return await self._db.run(_do)


__all__ = ["LocalEventLog"]
