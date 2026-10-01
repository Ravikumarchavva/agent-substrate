"""LocalSignalBus — SQLite-durable SignalBusProtocol (no-infra tier).

Consume-based, matching InMemorySignalBus's semantics exactly: ``signal()``
buffers a payload and wakes the target run via the scheduler if it's
suspended and waiting on that name; ``consume()`` claims one buffered
payload, exactly-once per ``effect_id``.

``timer()`` sets the scheduler's durable ``wake_at`` column instead of
spawning an ``asyncio.sleep`` task — see ``LocalScheduler.set_wake_at`` and
``InMemorySignalBus.timer``'s docstring for why (a durable timer must
survive a process restart; an in-process sleeping Task does not).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

from substrate.kernel.core.content import JsonObject
from substrate.kernel.runtime.ids import RunId

from ._local_db import LocalRuntimeDB
from ._local_scheduler import LocalScheduler


class LocalSignalBus:
    """SQLite-backed, consume-based SignalBusProtocol."""

    def __init__(self, db: LocalRuntimeDB, scheduler: LocalScheduler) -> None:
        self._db = db
        self._scheduler = scheduler

    async def signal(self, run_id: RunId, name: str, payload: JsonObject) -> None:
        payload_json = _dumps(payload)

        def _do(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT COALESCE(MAX(ord), -1) AS m FROM signal_buffer "
                "WHERE run_id = ? AND name = ?",
                (run_id, name),
            ).fetchone()
            conn.execute(
                "INSERT INTO signal_buffer (run_id, name, payload_json, ord) VALUES (?, ?, ?, ?)",
                (run_id, name, payload_json, row["m"] + 1),
            )

        await self._db.run(_do)

        wakeup = self._scheduler.wakeup_for(run_id)
        if wakeup is not None and wakeup.kind == "signal" and wakeup.signals:
            if name in wakeup.signals:
                await self._scheduler.wake_suspended(run_id)

    async def consume(
        self, run_id: RunId, name: str, effect_id: str
    ) -> JsonObject | None:
        def _do(conn: sqlite3.Connection) -> JsonObject | None:
            row = conn.execute(
                "SELECT payload_json FROM signal_consumed WHERE effect_id = ?",
                (effect_id,),
            ).fetchone()
            if row is not None:
                return _loads(row["payload_json"])

            next_row = conn.execute(
                "SELECT rowid, payload_json FROM signal_buffer "
                "WHERE run_id = ? AND name = ? ORDER BY ord LIMIT 1",
                (run_id, name),
            ).fetchone()
            if next_row is None:
                return None
            payload = _loads(next_row["payload_json"])
            conn.execute(
                "DELETE FROM signal_buffer WHERE rowid = ?", (next_row["rowid"],)
            )
            conn.execute(
                "INSERT INTO signal_consumed (effect_id, payload_json) VALUES (?, ?)",
                (effect_id, next_row["payload_json"]),
            )
            return payload

        return await self._db.run(_do)

    async def timer(self, run_id: RunId, at: datetime) -> None:
        await self._scheduler.set_wake_at(run_id, at)

    def gc(self, run_id: RunId) -> None:
        """Drop a terminal run's buffered-but-unclaimed signals.

        Sync, matching ``InMemorySignalBus.gc``'s signature (called from
        ``LocalSupervisor.finish_run`` without ``await``) — fires-and-
        forgets the delete on the shared connection directly, same
        WAL-safe-read reasoning as ``LocalScheduler``'s sync registry
        methods, just a write here: a stray gc that lands a beat late
        because it raced an in-flight writer transaction is harmless (the
        row is deleted eventually, and nothing reads it after this run is
        terminal), so it doesn't need the full lock/queue like a
        correctness-critical write does.
        """
        self._db._conn.execute("DELETE FROM signal_buffer WHERE run_id = ?", (run_id,))
        self._db._conn.commit()


def _dumps(payload: JsonObject) -> str:
    return json.dumps(payload)


def _loads(raw: str) -> JsonObject:
    return json.loads(raw)


__all__ = ["LocalSignalBus"]
