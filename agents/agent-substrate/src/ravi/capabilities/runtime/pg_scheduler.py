"""PostgresScheduler — Stage 1 durable Scheduler backed by asyncpg.

Schema::

    CREATE TABLE ravi_run_queue (
        run_id       TEXT        NOT NULL PRIMARY KEY,
        priority     INTEGER     NOT NULL DEFAULT 5,
        tenant       TEXT        NOT NULL DEFAULT 'default',
        status       TEXT        NOT NULL DEFAULT 'pending',
        worker_id    TEXT,
        expires_at   TIMESTAMPTZ,
        attempt      INTEGER     NOT NULL DEFAULT 0,
        retry_count  INTEGER     NOT NULL DEFAULT 0,
        wakeup       JSONB,
        retry_policy JSONB,
        enqueued_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE ravi_agent_runs (
        run_id   TEXT NOT NULL PRIMARY KEY,
        agent_id TEXT NOT NULL
    );
    CREATE INDEX ravi_agent_runs_agent_idx ON ravi_agent_runs(agent_id);

Lease acquisition uses ``SELECT … FOR UPDATE SKIP LOCKED`` to let multiple
workers poll concurrently without contention.  Expired leases are reclaimed
at the start of every ``lease()`` call.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, AsyncIterator

from ravi.kernel.core.identity import AgentId
from ravi.kernel.runtime.ids import RunId, RunStatus
from ravi.kernel.runtime.scheduler import Lease, RunRetryPolicy
from ravi.kernel.runtime.wakeup import Wakeup

if TYPE_CHECKING:
    import asyncpg

_LEASE_SECONDS = 30
_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS ravi_run_queue (
    run_id       TEXT        NOT NULL PRIMARY KEY,
    priority     INTEGER     NOT NULL DEFAULT 5,
    tenant       TEXT        NOT NULL DEFAULT 'default',
    status       TEXT        NOT NULL DEFAULT 'pending',
    worker_id    TEXT,
    expires_at   TIMESTAMPTZ,
    attempt      INTEGER     NOT NULL DEFAULT 0,
    retry_count  INTEGER     NOT NULL DEFAULT 0,
    wakeup       JSONB,
    retry_policy JSONB,
    enqueued_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ravi_run_queue_pending_idx
    ON ravi_run_queue (priority, enqueued_at)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS ravi_agent_runs (
    run_id   TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ravi_agent_runs_agent_idx
    ON ravi_agent_runs (agent_id);
"""

_STATUS_MAP: dict[str, RunStatus] = {
    "pending": RunStatus.PENDING,
    "running": RunStatus.RUNNING,
    "suspended": RunStatus.SUSPENDED,
    "completed": RunStatus.COMPLETED,
    "failed": RunStatus.FAILED,
    "cancelled": RunStatus.CANCELLED,
}
_STATUS_STR: dict[RunStatus, str] = {v: k for k, v in _STATUS_MAP.items()}
_TERMINAL = {"completed", "failed", "cancelled"}


class PostgresScheduler:
    """Postgres-backed Scheduler implementing the kernel Scheduler Protocol."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def setup(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_TABLES)

    # -- Extra methods (mirror InMemoryScheduler's non-Protocol surface) -----

    def register_run(self, run_id: RunId, agent_id: AgentId) -> None:
        """Synchronous registration stub — the actual INSERT happens lazily in enqueue.

        We store the mapping in a local dict first so that the first ``enqueue``
        call can persist it atomically alongside the queue entry.
        """
        self._pending_registrations[run_id] = agent_id

    def __init_extra(self) -> None:
        self._pending_registrations: dict[RunId, AgentId] = {}

    # Patch __init__ to also run __init_extra
    def __init__(self, pool: asyncpg.Pool) -> None:  # type: ignore[misc]
        self._pool = pool
        self._pending_registrations: dict[RunId, AgentId] = {}

    def agent_for(self, run_id: RunId) -> AgentId | None:
        return self._pending_registrations.get(run_id)

    def wakeup_for(self, run_id: RunId) -> Wakeup | None:
        return None  # loaded from DB on demand; Stage 1 does not cache

    async def get_status(self, run_id: RunId) -> RunStatus | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchval(
                "SELECT status FROM ravi_run_queue WHERE run_id = $1", run_id
            )
        return _STATUS_MAP.get(row) if row else None

    async def find_run_for_agent(
        self, agent_id: AgentId
    ) -> tuple[RunId, RunStatus] | None:
        """Return the most recent non-terminal (run_id, status) for agent_id."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT rq.run_id, rq.status
                FROM ravi_run_queue rq
                JOIN ravi_agent_runs ar USING (run_id)
                WHERE ar.agent_id = $1 AND rq.status NOT IN ('completed','failed','cancelled')
                ORDER BY rq.enqueued_at DESC
                LIMIT 1
                """,
                str(agent_id),
            )
        if row is None:
            return None
        return (RunId(row["run_id"]), _STATUS_MAP[row["status"]])

    async def wake_suspended(self, run_id: RunId, *, priority: int = 5) -> None:
        """Re-enqueue a suspended run."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ravi_run_queue
                SET status = 'pending', worker_id = NULL, expires_at = NULL
                WHERE run_id = $1 AND status = 'suspended'
                """,
                run_id,
            )

    # -- Scheduler Protocol --------------------------------------------------

    async def enqueue(
        self,
        run_id: RunId,
        *,
        priority: int,
        tenant: str,
        wake: Wakeup | None = None,
        retry_policy: RunRetryPolicy | None = None,
    ) -> None:
        agent_id = self._pending_registrations.pop(run_id, None)
        wakeup_json = wake.model_dump_json() if wake else None
        policy_json = retry_policy.model_dump_json() if retry_policy else None

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                if agent_id is not None:
                    await conn.execute(
                        """
                        INSERT INTO ravi_agent_runs (run_id, agent_id)
                        VALUES ($1, $2)
                        ON CONFLICT (run_id) DO NOTHING
                        """,
                        run_id,
                        str(agent_id),
                    )
                await conn.execute(
                    """
                    INSERT INTO ravi_run_queue
                        (run_id, priority, tenant, status, wakeup, retry_policy)
                    VALUES ($1, $2, $3, 'pending', $4::jsonb, $5::jsonb)
                    ON CONFLICT (run_id) DO UPDATE
                        SET wakeup = COALESCE(EXCLUDED.wakeup, ravi_run_queue.wakeup)
                        WHERE ravi_run_queue.status NOT IN ('pending','running')
                    """,
                    run_id,
                    priority,
                    tenant,
                    wakeup_json,
                    policy_json,
                )

    async def lease(self, *, worker_id: str, capacity: int) -> list[Lease]:
        expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=_LEASE_SECONDS)
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # Reclaim expired leases
                await conn.execute(
                    """
                    UPDATE ravi_run_queue
                    SET status = 'pending', worker_id = NULL, expires_at = NULL
                    WHERE status = 'running' AND expires_at < now()
                    """
                )
                # Claim up to capacity pending runs
                rows = await conn.fetch(
                    """
                    UPDATE ravi_run_queue
                    SET status = 'running', worker_id = $1, expires_at = $2
                    WHERE run_id IN (
                        SELECT run_id FROM ravi_run_queue
                        WHERE status = 'pending'
                        ORDER BY priority, enqueued_at
                        LIMIT $3
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING run_id, attempt
                    """,
                    worker_id,
                    expires_at,
                    capacity,
                )
        return [
            Lease(
                run_id=RunId(row["run_id"]),
                worker_id=worker_id,
                expires_at=expires_at,
                attempt=row["attempt"],
            )
            for row in rows
        ]

    async def heartbeat(self, lease: Lease) -> None:
        new_expires = datetime.now(tz=timezone.utc) + timedelta(seconds=_LEASE_SECONDS)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ravi_run_queue
                SET expires_at = $1
                WHERE run_id = $2 AND worker_id = $3 AND status = 'running'
                """,
                new_expires,
                lease.run_id,
                lease.worker_id,
            )

    async def release(
        self,
        lease: Lease,
        *,
        status: RunStatus,
        wake_on: Wakeup | None = None,
    ) -> None:
        status_str = _STATUS_STR[status]
        wakeup_json = wake_on.model_dump_json() if wake_on else None

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                if status == RunStatus.FAILED:
                    row = await conn.fetchrow(
                        """
                        UPDATE ravi_run_queue
                        SET retry_count = retry_count + 1
                        WHERE run_id = $1
                        RETURNING retry_count, retry_policy
                        """,
                        lease.run_id,
                    )
                    if row:
                        policy = (
                            RunRetryPolicy.model_validate(dict(row["retry_policy"]))
                            if row["retry_policy"]
                            else RunRetryPolicy()
                        )
                        if row["retry_count"] <= policy.max_retries:
                            await conn.execute(
                                """
                                UPDATE ravi_run_queue
                                SET status = 'pending', worker_id = NULL, expires_at = NULL
                                WHERE run_id = $1
                                """,
                                lease.run_id,
                            )
                            return

                await conn.execute(
                    """
                    UPDATE ravi_run_queue
                    SET status = $1,
                        worker_id = NULL,
                        expires_at = NULL,
                        wakeup = COALESCE($2::jsonb, wakeup)
                    WHERE run_id = $3
                    """,
                    status_str,
                    wakeup_json,
                    lease.run_id,
                )

    async def pending_runs(
        self,
        *,
        tenant: str | None = None,
    ) -> AsyncIterator[RunId]:
        return self._pending_iter(tenant)

    async def _pending_iter(
        self, tenant: str | None
    ) -> AsyncIterator[RunId]:  # type: ignore[return]
        q = "SELECT run_id FROM ravi_run_queue WHERE status = 'pending'"
        args: list[object] = []
        if tenant is not None:
            q += " AND tenant = $1"
            args.append(tenant)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(q, *args)
        for row in rows:
            yield RunId(row["run_id"])


__all__ = ["PostgresScheduler"]
