"""LocalScheduler — SQLite-durable SchedulerProtocol + RunRegistryProtocol
(no-infra tier).

Mirrors ``infrastructure/runtime/scheduler.py``'s durable design (a
``run_queue`` table, a durable ``wake_at`` column instead of an in-process
timer, retry backoff via suspend+wake_at rather than ``asyncio.sleep``) —
just SQLite SQL instead of asyncpg SQL, and single-writer-lock atomicity
instead of ``SELECT ... FOR UPDATE SKIP LOCKED``. See ``_local_db.py`` for
why that's still the same contract, not a downgraded one.

``agent_for``/``wakeup_for``/``register_run`` are synchronous (not
``async def``) to match ``InMemoryScheduler``'s exact signature — callers
like ``LocalSignalBus.signal()`` call them without ``await``. They read
directly off the shared connection (no lock, no thread hop): safe under
WAL journal mode, where a reader always sees the last *committed* snapshot
and never a partially-written transaction, even while another thread holds
the write lock via ``LocalRuntimeDB.run()``.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from substrate.infrastructure.observability.runtime_metrics import (
    retry_counter,
    suspension_counter,
)
from substrate.kernel.agent.supervision import Priority
from substrate.kernel.core.identity import Actor
from substrate.kernel.exceptions import ThreadBusyError
from substrate.kernel.runtime.ids import RunId, RunStatus
from substrate.kernel.runtime.scheduler import Lease, RunRetryPolicy
from substrate.kernel.runtime.wakeup import Wakeup

from ._local_db import LocalRuntimeDB

_LEASE_SECONDS = 30
_TERMINAL = {"completed", "failed", "cancelled"}
_STATUS_STR: dict[RunStatus, str] = {
    RunStatus.PENDING: "pending",
    RunStatus.RUNNING: "running",
    RunStatus.SUSPENDED: "suspended",
    RunStatus.COMPLETED: "completed",
    RunStatus.FAILED: "failed",
    RunStatus.CANCELLED: "cancelled",
}
_STATUS_ENUM: dict[str, RunStatus] = {v: k for k, v in _STATUS_STR.items()}


def _retry_backoff_seconds(retry_count: int, policy: RunRetryPolicy) -> float:
    delay = policy.backoff_s * (2 ** max(retry_count - 1, 0))
    return min(delay, policy.max_backoff_s)


def _now_ts() -> float:
    return datetime.now(tz=timezone.utc).timestamp()


def _to_ts(dt: datetime) -> float:
    return dt.timestamp()


class LocalScheduler:
    """SQLite-backed SchedulerProtocol + RunRegistryProtocol."""

    def __init__(self, db: LocalRuntimeDB) -> None:
        self._db = db

    # ── RunRegistryProtocol: sync, direct-read (see module docstring) ───────

    def register_run(self, run_id: RunId, agent_id: Actor) -> None:
        # Placeholder status 'registered' — NOT 'pending'. enqueue() checks
        # for status in ('pending', 'running') to decide whether this is a
        # fresh enqueue (must hit the thread-uniqueness constraint) or a
        # coalesce of an already-enqueued run; a 'registered'-but-never-
        # enqueued row must always take the fresh-enqueue path.
        self._db._conn.execute(
            "INSERT INTO run_queue (run_id, agent_id, status, enqueued_at) "
            "VALUES (?, ?, 'registered', ?) "
            "ON CONFLICT (run_id) DO UPDATE SET agent_id = excluded.agent_id",
            (run_id, str(agent_id), _now_ts()),
        )
        self._db._conn.commit()

    def agent_for(self, run_id: RunId) -> Actor | None:
        row = self._db._conn.execute(
            "SELECT agent_id FROM run_queue WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None or row["agent_id"] is None:
            return None
        return Actor.from_str(row["agent_id"])

    def wakeup_for(self, run_id: RunId) -> Wakeup | None:
        row = self._db._conn.execute(
            "SELECT wakeup_json FROM run_queue WHERE run_id = ?", (run_id,)
        ).fetchone()
        if row is None or row["wakeup_json"] is None:
            return None
        return Wakeup.model_validate_json(row["wakeup_json"])

    # ── SchedulerProtocol ─────────────────────────────────────────────────

    async def enqueue(
        self,
        run_id: RunId,
        *,
        priority: Priority = Priority.NORMAL,
        tenant: str,
        wake: Wakeup | None = None,
        retry_policy: RunRetryPolicy | None = None,
        deadline: datetime | None = None,
        thread_id: str | None = None,
    ) -> None:
        wake_json = wake.model_dump_json() if wake else None
        policy_json = retry_policy.model_dump_json() if retry_policy else None
        deadline_ts = _to_ts(deadline) if deadline else None

        def _do(conn: sqlite3.Connection) -> None:
            row = conn.execute(
                "SELECT status FROM run_queue WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is not None and row["status"] in ("pending", "running"):
                # Coalesce: merge wakeup but don't add a duplicate entry.
                if wake is not None:
                    conn.execute(
                        "UPDATE run_queue SET wakeup_json = ? WHERE run_id = ?",
                        (wake_json, run_id),
                    )
                return
            try:
                conn.execute(
                    "UPDATE run_queue SET status = 'pending', priority = ?, "
                    "tenant = ?, wakeup_json = ?, retry_policy_json = COALESCE(?, retry_policy_json), "
                    "deadline = ?, thread_id = ?, enqueued_at = ?, worker_id = NULL, "
                    "expires_at = NULL, wake_at = NULL "
                    "WHERE run_id = ?",
                    (
                        int(priority),
                        tenant,
                        wake_json,
                        policy_json,
                        deadline_ts,
                        thread_id,
                        _now_ts(),
                        run_id,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if thread_id is not None and "run_queue.thread_id" in str(exc):
                    raise ThreadBusyError(
                        f"thread {thread_id} already has an active run",
                        thread_id=thread_id,
                    ) from exc
                raise

        await self._db.run(_do)

    async def find_run_for_thread(
        self, thread_id: str
    ) -> tuple[RunId, RunStatus] | None:
        def _do(conn: sqlite3.Connection) -> tuple[str, str] | None:
            row = conn.execute(
                "SELECT run_id, status FROM run_queue WHERE thread_id = ? "
                "AND status NOT IN ('completed', 'failed', 'cancelled') LIMIT 1",
                (thread_id,),
            ).fetchone()
            return (row["run_id"], row["status"]) if row else None

        result = await self._db.run(_do)
        if result is None:
            return None
        run_id, status = result
        return (RunId(run_id), _STATUS_ENUM[status])

    async def find_all_runs_for_thread(self, thread_id: str) -> list[RunId]:
        def _do(conn: sqlite3.Connection) -> list[str]:
            rows = conn.execute(
                "SELECT run_id FROM run_queue WHERE thread_id = ? ORDER BY enqueued_at",
                (thread_id,),
            ).fetchall()
            return [r["run_id"] for r in rows]

        return [RunId(r) for r in await self._db.run(_do)]

    async def lease(self, *, worker_id: str, capacity: int) -> list[Lease]:
        expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=_LEASE_SECONDS)
        expires_ts = _to_ts(expires_at)
        now_ts = _now_ts()

        def _do(conn: sqlite3.Connection) -> list[sqlite3.Row]:
            # Reclaim expired leases.
            conn.execute(
                "UPDATE run_queue SET status = 'pending', worker_id = NULL, expires_at = NULL "
                "WHERE status = 'running' AND expires_at IS NOT NULL AND expires_at < ?",
                (now_ts,),
            )
            # Wake suspended runs whose wake_at timer has elapsed.
            conn.execute(
                "UPDATE run_queue SET status = 'pending', worker_id = NULL, expires_at = NULL "
                "WHERE status = 'suspended' AND wake_at IS NOT NULL AND wake_at <= ?",
                (now_ts,),
            )
            # Terminate pending/suspended runs that exceeded their deadline.
            conn.execute(
                "UPDATE run_queue SET status = 'failed', worker_id = NULL, expires_at = NULL, "
                "wakeup_json = NULL, wake_at = NULL, terminated_at = ? "
                "WHERE status IN ('pending', 'suspended') "
                "AND deadline IS NOT NULL AND deadline <= ?",
                (now_ts, now_ts),
            )
            # Claim up to `capacity` pending runs, fair-share by tenant.
            candidates = conn.execute(
                "SELECT run_id FROM ("
                "  SELECT run_id, tenant, "
                "         ROW_NUMBER() OVER (PARTITION BY tenant ORDER BY priority, enqueued_at) AS rn "
                "  FROM run_queue WHERE status = 'pending'"
                ") ORDER BY rn, run_id LIMIT ?",
                (capacity,),
            ).fetchall()
            claimed: list[sqlite3.Row] = []
            for row in candidates:
                run_id = row["run_id"]
                conn.execute(
                    "UPDATE run_queue SET status = 'running', worker_id = ?, expires_at = ? "
                    "WHERE run_id = ?",
                    (worker_id, expires_ts, run_id),
                )
                claimed.append(
                    conn.execute(
                        "SELECT run_id, attempt, tenant, agent_id FROM run_queue WHERE run_id = ?",
                        (run_id,),
                    ).fetchone()
                )
            return claimed

        rows = await self._db.run(_do)
        leases: list[Lease] = []
        for row in rows:
            if row["agent_id"] is None:
                continue
            leases.append(
                Lease(
                    run_id=RunId(row["run_id"]),
                    agent_id=Actor.from_str(row["agent_id"]),
                    worker_id=worker_id,
                    expires_at=expires_at,
                    attempt=row["attempt"],
                    tenant=row["tenant"],
                )
            )
        return leases

    async def heartbeat(self, lease: Lease) -> bool:
        new_expires = _to_ts(
            datetime.now(tz=timezone.utc) + timedelta(seconds=_LEASE_SECONDS)
        )

        def _do(conn: sqlite3.Connection) -> bool:
            cur = conn.execute(
                "UPDATE run_queue SET expires_at = ? "
                "WHERE run_id = ? AND worker_id = ? AND status = 'running'",
                (new_expires, lease.run_id, lease.worker_id),
            )
            return cur.rowcount > 0

        return await self._db.run(_do)

    async def release(
        self,
        lease: Lease,
        *,
        status: RunStatus,
        wake_on: Wakeup | None = None,
        retryable: bool = True,
    ) -> bool:
        status_str = _STATUS_STR[status]

        def _do(conn: sqlite3.Connection) -> bool:
            if status == RunStatus.FAILED and retryable:
                row = conn.execute(
                    "SELECT retry_count, retry_policy_json, tenant FROM run_queue WHERE run_id = ?",
                    (lease.run_id,),
                ).fetchone()
                policy = (
                    RunRetryPolicy.model_validate_json(row["retry_policy_json"])
                    if row and row["retry_policy_json"]
                    else RunRetryPolicy()
                )
                count = (row["retry_count"] if row else 0) + 1
                if count <= policy.max_retries:
                    delay = _retry_backoff_seconds(count, policy)
                    wake_at = _to_ts(
                        datetime.now(tz=timezone.utc) + timedelta(seconds=delay)
                    )
                    conn.execute(
                        "UPDATE run_queue SET status = 'suspended', worker_id = NULL, "
                        "expires_at = NULL, retry_count = ?, wake_at = ? WHERE run_id = ?",
                        (count, wake_at, lease.run_id),
                    )
                    retry_counter.add(1, {"backend": "local"})
                    return False

            conn.execute(
                "UPDATE run_queue SET status = ?, worker_id = NULL, expires_at = NULL "
                "WHERE run_id = ?",
                (status_str, lease.run_id),
            )

            if status == RunStatus.SUSPENDED and wake_on:
                wake_at = _to_ts(wake_on.at) if wake_on.at else None
                conn.execute(
                    "UPDATE run_queue SET wakeup_json = ?, wake_at = ? WHERE run_id = ?",
                    (wake_on.model_dump_json(), wake_at, lease.run_id),
                )

            if status == RunStatus.SUSPENDED:
                suspension_counter.add(1, {"backend": "local"})

            return status != RunStatus.SUSPENDED

        return await self._db.run(_do)

    async def pending_runs(self, *, tenant: str | None = None) -> AsyncIterator[RunId]:
        return self._pending_iter(tenant)

    async def _pending_iter(self, tenant: str | None) -> AsyncIterator[RunId]:  # type: ignore[return]
        def _do(conn: sqlite3.Connection) -> list[str]:
            if tenant is not None:
                rows = conn.execute(
                    "SELECT run_id FROM run_queue WHERE status = 'pending' AND tenant = ?",
                    (tenant,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT run_id FROM run_queue WHERE status = 'pending'"
                ).fetchall()
            return [r["run_id"] for r in rows]

        for run_id in await self._db.run(_do):
            yield RunId(run_id)

    async def get_status(self, run_id: RunId) -> RunStatus | None:
        def _do(conn: sqlite3.Connection) -> str | None:
            row = conn.execute(
                "SELECT status FROM run_queue WHERE run_id = ?", (run_id,)
            ).fetchone()
            return row["status"] if row else None

        status = await self._db.run(_do)
        return _STATUS_ENUM[status] if status else None

    async def cancel_pending(self, run_id: RunId) -> bool:
        def _do(conn: sqlite3.Connection) -> bool:
            row = conn.execute(
                "SELECT status FROM run_queue WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is not None and row["status"] == "running":
                return False
            conn.execute(
                "UPDATE run_queue SET status = 'cancelled' WHERE run_id = ?", (run_id,)
            )
            return True

        return await self._db.run(_do)

    async def force_cancel(self, run_id: RunId) -> None:
        """Unconditionally mark *run_id* CANCELLED — see InMemoryScheduler's
        sibling method for why ``Supervisor.cancel()`` needs this instead of
        the guarded ``cancel_pending``."""

        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE run_queue SET status = 'cancelled' WHERE run_id = ?", (run_id,)
            )

        await self._db.run(_do)

    async def wake_suspended(
        self, run_id: RunId, *, priority: Priority = Priority.NORMAL
    ) -> None:
        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE run_queue SET status = 'pending', priority = ?, "
                "worker_id = NULL, expires_at = NULL, wake_at = NULL "
                "WHERE run_id = ? AND status = 'suspended'",
                (int(priority), run_id),
            )

        await self._db.run(_do)

    async def find_run_by_wake_signal(self, signal_name: str) -> RunId | None:
        def _do(conn: sqlite3.Connection) -> str | None:
            rows = conn.execute(
                "SELECT run_id, wakeup_json FROM run_queue "
                "WHERE status = 'suspended' AND wakeup_json IS NOT NULL"
            ).fetchall()
            for row in rows:
                wakeup = Wakeup.model_validate_json(row["wakeup_json"])
                if wakeup.signals and signal_name in wakeup.signals:
                    return row["run_id"]
            return None

        run_id = await self._db.run(_do)
        return RunId(run_id) if run_id else None

    async def find_run_for_agent(
        self, agent_id: Actor
    ) -> tuple[RunId, RunStatus] | None:
        def _do(conn: sqlite3.Connection) -> tuple[str, str] | None:
            row = conn.execute(
                "SELECT run_id, status FROM run_queue WHERE agent_id = ? "
                "AND status NOT IN ('completed', 'failed', 'cancelled') "
                "ORDER BY enqueued_at DESC LIMIT 1",
                (str(agent_id),),
            ).fetchone()
            return (row["run_id"], row["status"]) if row else None

        result = await self._db.run(_do)
        if result is None:
            return None
        run_id, status = result
        return (RunId(run_id), _STATUS_ENUM[status])

    async def wake_agent(
        self, agent_id: Actor, *, priority: Priority = Priority.NORMAL
    ) -> None:
        found = await self.find_run_for_agent(agent_id)
        if found is not None:
            run_id, status = found
            if status == RunStatus.SUSPENDED:
                await self.wake_suspended(run_id, priority=priority)

    async def set_wake_at(self, run_id: RunId, at: datetime) -> None:
        """Durable timer for ``LocalSignalBus.timer()``: a suspended run's
        ``wake_at`` is picked up by the next ``lease()`` poll — no
        in-process sleeping task, so it survives a restart, mirroring how
        the Postgres tier's own ``timer()`` works (see that module)."""

        def _do(conn: sqlite3.Connection) -> None:
            conn.execute(
                "UPDATE run_queue SET wake_at = ? WHERE run_id = ? AND status = 'suspended'",
                (_to_ts(at), run_id),
            )

        await self._db.run(_do)


__all__ = ["LocalScheduler"]
