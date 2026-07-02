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

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, AsyncIterator

from substrate.kernel.core.identity import AgentId
from substrate.kernel.runtime.ids import RunId, RunStatus
from substrate.kernel.runtime.scheduler import Lease, RunRetryPolicy
from substrate.kernel.runtime.wakeup import Wakeup

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
    enqueued_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    wake_signals TEXT[],
    wake_at      TIMESTAMPTZ,
    cancel_requested BOOLEAN NOT NULL DEFAULT false,
    deadline     TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS ravi_run_queue_pending_idx
    ON ravi_run_queue (priority, enqueued_at)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS ravi_agent_runs (
    run_id   TEXT NOT NULL PRIMARY KEY,
    agent_id TEXT NOT NULL,
    spec     JSONB
);
CREATE INDEX IF NOT EXISTS ravi_agent_runs_agent_idx
    ON ravi_agent_runs (agent_id);
"""

# Columns added after the original table shape shipped — additive migration
# for pre-existing deployments; CREATE TABLE above already includes them for
# fresh ones. Must run BEFORE any index referencing these columns: on a
# pre-existing table, "CREATE TABLE IF NOT EXISTS" in _CREATE_TABLES is a
# no-op and never adds them, so an index created in that same statement
# would reference a column that doesn't exist yet.
_MIGRATE_COLUMNS: list[tuple[str, str]] = [
    ("wake_signals", "TEXT[]"),
    ("wake_at", "TIMESTAMPTZ"),
    ("cancel_requested", "BOOLEAN NOT NULL DEFAULT false"),
    ("deadline", "TIMESTAMPTZ"),
    # Set once, when a run first reaches a terminal status — the retention
    # sweep's cutoff (see infrastructure/runtime/retention.py). NULL for any
    # non-terminal run.
    ("terminated_at", "TIMESTAMPTZ"),
]

_CREATE_INDEXES_POST_MIGRATION = """
CREATE INDEX IF NOT EXISTS ravi_run_queue_wake_at_idx
    ON ravi_run_queue (wake_at)
    WHERE status = 'suspended';
CREATE INDEX IF NOT EXISTS ravi_run_queue_terminated_at_idx
    ON ravi_run_queue (terminated_at)
    WHERE terminated_at IS NOT NULL;
"""

_TERMINAL_STATUSES = ("completed", "failed", "cancelled")

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
        self._pending_registrations: dict[RunId, AgentId] = {}

    async def setup(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_TABLES)
            for col, defn in _MIGRATE_COLUMNS:
                await conn.execute(
                    f"ALTER TABLE ravi_run_queue ADD COLUMN IF NOT EXISTS {col} {defn}"
                )
            await conn.execute(_CREATE_INDEXES_POST_MIGRATION)

    async def save_run_spec(self, run_id: RunId, spec: dict) -> None:
        """Persist the agent spec for a run so it can be rebuilt on cold resume."""
        import json

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO ravi_agent_runs (run_id, agent_id, spec)
                VALUES ($1, '', $2::jsonb)
                ON CONFLICT (run_id) DO UPDATE SET spec = EXCLUDED.spec
                """,
                run_id,
                json.dumps(spec),
            )

    async def pending_run_specs(self) -> list[tuple[RunId, AgentId, dict]]:
        """Return (run_id, agent_id, spec) for all pending runs that have a spec.

        Used by the cold-resume hook to rebuild and register agents for orphaned
        runs that were requeued by reclaim_orphans().
        """
        import json

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT rq.run_id, ar.agent_id, ar.spec
                FROM ravi_run_queue rq
                JOIN ravi_agent_runs ar USING (run_id)
                WHERE rq.status = 'pending' AND ar.spec IS NOT NULL
                """
            )
        result: list[tuple[RunId, AgentId, dict]] = []
        for row in rows:
            type_, _, key = row["agent_id"].partition("/")
            spec = (
                json.loads(row["spec"])
                if isinstance(row["spec"], str)
                else dict(row["spec"])
            )
            result.append((RunId(row["run_id"]), AgentId(type=type_, key=key), spec))
        return result

    async def reclaim_orphans(self, *, all_running: bool = False) -> int:
        """Requeue runs left ``running`` by a crashed worker, at startup.

        ``all_running=False`` (default, multi-worker safe): only leases whose
        ``expires_at`` has passed — never steals a live peer's run.
        ``all_running=True`` (single-worker deployments, e.g. the monolith): a
        fresh process owns no leases, so every ``running`` row is orphaned and
        is requeued immediately rather than waiting out the lease.

        Requeued runs become ``pending``; when their agent is (re-)registered
        the worker leases them and the kernel replays from the EventLog (the
        journal makes completed effects at-most-once).  Returns the count.
        """
        where = "status = 'running'"
        if not all_running:
            where += " AND expires_at < now()"
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                f"""
                UPDATE ravi_run_queue
                SET status = 'pending', worker_id = NULL, expires_at = NULL
                WHERE {where}
                """
            )
        # asyncpg returns e.g. "UPDATE 3"
        try:
            return int(result.split()[-1])
        except (ValueError, IndexError):
            return 0

    def register_run(self, run_id: RunId, agent_id: AgentId) -> None:
        """The actual INSERT happens lazily in enqueue; store mapping here first."""
        self._pending_registrations[run_id] = agent_id

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

    async def wake_agent(self, agent_id: AgentId, *, priority: int = 5) -> None:
        """Re-enqueue the active suspended run for agent_id, if any."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE ravi_run_queue rq
                SET status = 'pending', worker_id = NULL, expires_at = NULL
                WHERE rq.status = 'suspended'
                  AND rq.run_id IN (
                      SELECT run_id FROM ravi_agent_runs WHERE agent_id = $1
                  )
                """,
                str(agent_id),
            )

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
        deadline: datetime | None = None,
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
                        (run_id, priority, tenant, status, wakeup, retry_policy, deadline)
                    VALUES ($1, $2, $3, 'pending', $4::jsonb, $5::jsonb, $6)
                    ON CONFLICT (run_id) DO UPDATE
                        SET wakeup = COALESCE(EXCLUDED.wakeup, ravi_run_queue.wakeup)
                        WHERE ravi_run_queue.status NOT IN ('pending','running')
                    """,
                    run_id,
                    priority,
                    tenant,
                    wakeup_json,
                    policy_json,
                    deadline,
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
                # Timer/deadline wakeups ride this same poll cadence — no
                # separate timer service. A suspended run whose wake_at has
                # passed is due; wake_signals is left as-is (harmless once
                # pending — the wait will consume() and, if nothing's there
                # yet, immediately re-suspend on the next iteration).
                await conn.execute(
                    """
                    UPDATE ravi_run_queue
                    SET status = 'pending', worker_id = NULL, expires_at = NULL
                    WHERE status = 'suspended' AND wake_at IS NOT NULL AND wake_at <= now()
                    """
                )
                # Deadline enforcement: a run stuck pending or suspended past
                # its deadline (no worker ever running it to observe a
                # heartbeat) terminates here directly — a coarser circuit
                # breaker than any single ctx.ask/ctx.join timeout. Running
                # runs are caught by heartbeat() instead (see there).
                await conn.execute(
                    """
                    UPDATE ravi_run_queue
                    SET status = 'failed', worker_id = NULL, expires_at = NULL,
                        wake_signals = NULL, wake_at = NULL, terminated_at = now()
                    WHERE status IN ('pending', 'suspended')
                      AND deadline IS NOT NULL AND deadline <= now()
                    """
                )
                # Claim up to capacity pending runs
                rows = await conn.fetch(
                    """
                    UPDATE ravi_run_queue rq
                    SET status = 'running', worker_id = $1, expires_at = $2
                    WHERE rq.run_id IN (
                        SELECT run_id FROM ravi_run_queue
                        WHERE status = 'pending'
                        ORDER BY priority, enqueued_at
                        LIMIT $3
                        FOR UPDATE SKIP LOCKED
                    )
                    RETURNING rq.run_id, rq.attempt,
                        (SELECT agent_id FROM ravi_agent_runs WHERE run_id = rq.run_id) AS agent_id
                    """,
                    worker_id,
                    expires_at,
                    capacity,
                )
        leases: list[Lease] = []
        for row in rows:
            raw_aid: str | None = row["agent_id"]
            if raw_aid is None:
                continue  # no agent registered — skip
            type_, _, key = raw_aid.partition("/")
            agent_id = AgentId(type=type_, key=key)
            leases.append(
                Lease(
                    run_id=RunId(row["run_id"]),
                    agent_id=agent_id,
                    worker_id=worker_id,
                    expires_at=expires_at,
                    attempt=row["attempt"],
                )
            )
        return leases

    async def heartbeat(self, lease: Lease) -> bool:
        new_expires = datetime.now(tz=timezone.utc) + timedelta(seconds=_LEASE_SECONDS)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE ravi_run_queue
                SET expires_at = $1
                WHERE run_id = $2 AND worker_id = $3 AND status = 'running'
                RETURNING cancel_requested, deadline
                """,
                new_expires,
                lease.run_id,
                lease.worker_id,
            )
        if row is None:
            return False
        if row["cancel_requested"]:
            return True
        deadline = row["deadline"]
        return deadline is not None and datetime.now(tz=timezone.utc) >= deadline

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
                        import json

                        raw = row["retry_policy"]
                        if raw:
                            policy_data = (
                                json.loads(raw) if isinstance(raw, str) else dict(raw)
                            )
                            policy = RunRetryPolicy.model_validate(policy_data)
                        else:
                            policy = RunRetryPolicy()
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

                if status == RunStatus.SUSPENDED:
                    # wake_signals and wake_at are orthogonal, not kind-exclusive:
                    # ctx.ask() suspends on BOTH a set of signal names AND a
                    # deadline at once (whichever comes first should wake it),
                    # so a Wakeup can legitimately carry both regardless of its
                    # nominal "kind".
                    wake_signals = wake_on.signals if wake_on and wake_on.signals else None
                    wake_at = wake_on.at if wake_on else None
                    await conn.execute(
                        """
                        UPDATE ravi_run_queue
                        SET status = 'suspended',
                            worker_id = NULL,
                            expires_at = NULL,
                            wakeup = COALESCE($1::jsonb, wakeup),
                            wake_signals = $2,
                            wake_at = $3
                        WHERE run_id = $4
                        """,
                        wakeup_json,
                        wake_signals,
                        wake_at,
                        lease.run_id,
                    )
                    if wake_signals:
                        # Close the lost-wakeup race: a signal may have
                        # arrived between the miss inside RunContext (which
                        # is why we're suspending) and this UPDATE landing.
                        # If so, un-suspend immediately instead of parking
                        # on a wakeup that already happened.
                        pending = await conn.fetchval(
                            """
                            SELECT 1 FROM ravi_signals
                            WHERE run_id = $1 AND name = ANY($2)
                              AND consumed_at IS NULL
                            LIMIT 1
                            """,
                            lease.run_id,
                            wake_signals,
                        )
                        if pending:
                            await conn.execute(
                                """
                                UPDATE ravi_run_queue
                                SET status = 'pending', worker_id = NULL, expires_at = NULL
                                WHERE run_id = $1
                                """,
                                lease.run_id,
                            )
                    return

                # This is always a terminal status here (SUSPENDED and the
                # FAILED-with-retries-remaining case both returned above) —
                # stamp terminated_at for the retention sweep's cutoff.
                await conn.execute(
                    """
                    UPDATE ravi_run_queue
                    SET status = $1,
                        worker_id = NULL,
                        expires_at = NULL,
                        wakeup = COALESCE($2::jsonb, wakeup),
                        terminated_at = now()
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

    async def _pending_iter(self, tenant: str | None) -> AsyncIterator[RunId]:  # type: ignore[return]
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
