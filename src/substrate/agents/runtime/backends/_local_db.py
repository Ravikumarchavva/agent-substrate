"""Shared SQLite connection for the local-filesystem-durable runtime backends.

Why SQLite, and what it actually promises
------------------------------------------
The in-memory backends (``_event_log.py``, ``_scheduler.py``, etc.) are
Stage 0: correct, but gone on process restart. The Postgres backends
(``infrastructure/runtime/``) are Stage 1: durable, and safe for many worker
*processes* across *machines* via row-level locking (``SELECT ... FOR UPDATE
SKIP LOCKED``). This module is a third tier — durable with zero external
infrastructure — for the case the user explicitly asked for: "even without
docker or postgres, the minimum is a folder and everything dumps there."

It satisfies the exact same kernel Protocols (``SchedulerProtocol``,
``RunRegistryProtocol``, ``EventLogProtocol``, ``InboxProtocol``,
``SupervisorProtocol``, ``SignalBusProtocol``, ``FollowGraph``) with the same
correctness guarantees (atomic claim, no double-dispatch, durable across
restarts) as the Postgres tier — one abstraction, same contract, regardless
of which backend is plugged in. The one honest constraint, inherent to being
a local file rather than a network service: safe for multiple *processes on
one host* sharing this file (SQLite's OS-level file locking works across
processes, not just threads), not network-distributed across machines. A
deployment that needs multi-machine workers should use the Postgres tier;
this tier is for the no-infra / single-host case the in-memory tier can't
cover because it doesn't survive a restart.

Concurrency model
------------------
SQLite allows only one writer at a time. Rather than fight that, this module
embraces it: every operation across every backend sharing one
``LocalRuntimeDB`` instance is serialized through one ``asyncio.Lock`` plus
one ``BEGIN IMMEDIATE`` transaction per operation, so a whole multi-statement
operation (e.g. "reclaim expired leases, then claim up to N pending runs")
runs atomically with no other coroutine — and, via SQLite's own file lock,
no other process — able to interleave. This trades throughput for
simplicity and correctness, which is the right trade for the deployment tier
this exists for (a single dev box or small single-node deployment, not a
high-throughput cluster).
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Callable, TypeVar

T = TypeVar("T")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS run_queue (
    run_id       TEXT NOT NULL PRIMARY KEY,
    agent_id     TEXT,
    priority     INTEGER NOT NULL DEFAULT 5,
    tenant       TEXT NOT NULL DEFAULT 'default',
    status       TEXT NOT NULL DEFAULT 'pending',
    worker_id    TEXT,
    expires_at   TEXT,
    attempt      INTEGER NOT NULL DEFAULT 0,
    retry_count  INTEGER NOT NULL DEFAULT 0,
    wakeup_json  TEXT,
    wake_at      TEXT,
    retry_policy_json TEXT,
    thread_id    TEXT,
    deadline     TEXT,
    terminated_at TEXT,
    enqueued_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS run_queue_pending_idx
    ON run_queue (priority, enqueued_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS run_queue_wake_at_idx
    ON run_queue (wake_at) WHERE status = 'suspended';
-- Durable single-flight: at most one non-terminal run per thread_id.
CREATE UNIQUE INDEX IF NOT EXISTS run_queue_thread_singleflight_idx
    ON run_queue (thread_id)
    WHERE thread_id IS NOT NULL AND status IN ('pending', 'running', 'suspended');

CREATE TABLE IF NOT EXISTS event_log (
    run_id TEXT NOT NULL,
    seq    INTEGER NOT NULL,
    entry_json TEXT NOT NULL,
    PRIMARY KEY (run_id, seq)
);

CREATE TABLE IF NOT EXISTS inbox_messages (
    agent_id  TEXT NOT NULL,
    msg_id    TEXT NOT NULL,
    sender_key TEXT NOT NULL,
    msg_json  TEXT NOT NULL,
    ord       INTEGER NOT NULL,
    PRIMARY KEY (agent_id, msg_id)
);
CREATE INDEX IF NOT EXISTS inbox_order_idx ON inbox_messages (agent_id, sender_key, ord);

CREATE TABLE IF NOT EXISTS inbox_retries (
    agent_id TEXT NOT NULL,
    msg_id   TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (agent_id, msg_id)
);

CREATE TABLE IF NOT EXISTS inbox_dead_letters (
    agent_id TEXT NOT NULL,
    msg_id   TEXT NOT NULL,
    entry_json TEXT NOT NULL,
    ord      INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS follow_edges (
    topic    TEXT NOT NULL,
    follower TEXT NOT NULL,
    PRIMARY KEY (topic, follower)
);

CREATE TABLE IF NOT EXISTS signal_buffer (
    run_id  TEXT NOT NULL,
    name    TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    ord     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS signal_buffer_idx ON signal_buffer (run_id, name, ord);

CREATE TABLE IF NOT EXISTS signal_consumed (
    effect_id TEXT NOT NULL PRIMARY KEY,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supervisor_spawn_effects (
    effect_id TEXT NOT NULL PRIMARY KEY,
    child_run_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supervisor_children (
    parent_run_id TEXT NOT NULL,
    child_run_id  TEXT NOT NULL,
    handle_json   TEXT NOT NULL,
    PRIMARY KEY (parent_run_id, child_run_id)
);
CREATE INDEX IF NOT EXISTS supervisor_parent_of_idx ON supervisor_children (child_run_id);

CREATE TABLE IF NOT EXISTS supervisor_supervision (
    run_id TEXT NOT NULL PRIMARY KEY,
    supervision_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS supervisor_results (
    run_id TEXT NOT NULL PRIMARY KEY,
    result_json TEXT NOT NULL
);
"""


class LocalRuntimeDB:
    """One SQLite file, one connection, one write lock — shared by every
    local-filesystem runtime backend so a multi-statement operation from any
    of them is atomic with respect to every other."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA_SQL)
        self._conn.commit()
        self._lock = asyncio.Lock()

    async def run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run *fn* against the connection inside one ``BEGIN IMMEDIATE``
        transaction, serialized against every other caller of this method on
        this instance. *fn* is synchronous — it must not ``await``."""
        async with self._lock:
            return await asyncio.to_thread(self._run_sync, fn)

    def _run_sync(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            result = fn(self._conn)
            self._conn.commit()
            return result
        except BaseException:
            self._conn.rollback()
            raise

    def close(self) -> None:
        self._conn.close()


__all__ = ["LocalRuntimeDB"]
