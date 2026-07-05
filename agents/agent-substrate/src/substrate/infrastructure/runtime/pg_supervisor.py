"""PostgresSupervisor — Stage 1 durable Supervisor backed by asyncpg.

Schema::

    CREATE TABLE ravi_run_tree (
        run_id      TEXT PRIMARY KEY,
        parent_run  TEXT,
        root_run    TEXT NOT NULL,
        agent_id    TEXT NOT NULL,
        status      TEXT NOT NULL DEFAULT 'pending',
        error       TEXT,
        created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
    );

    CREATE TABLE ravi_spawn_effects (
        effect_id     TEXT PRIMARY KEY,
        child_run_id  TEXT NOT NULL
    );

``spawn`` is idempotent against ``ravi_spawn_effects``, keyed by the caller's
replay-stable effect_id (derived from ``RunContext._alloc_path()`` — see the
kernel ``Supervisor.spawn()`` docstring for why it must never be computed
fresh). ``finish_run`` is the durable completion path: it marks the run
terminal in ``ravi_run_tree`` and fires a ``child:{run_id}`` signal to the
parent — that's what a suspended ``ctx.join``/``ctx.ask`` consumes to resume,
closing the "parent stalls the full timeout waiting on a crashed child" gap.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, AsyncIterator

from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import Message
from substrate.kernel.runtime.effects import Effect
from substrate.kernel.runtime.ids import RunId, RunStatus, new_run_id
from substrate.kernel.runtime.log_entry import RunLogEntry
from substrate.kernel.runtime.supervisor import RunHandle, RunResult
from substrate.kernel.agent.supervision import Supervision

if TYPE_CHECKING:
    import asyncpg
    from substrate.infrastructure.runtime.pg_event_log import PostgresEventLog
    from substrate.infrastructure.runtime.pg_inbox import PostgresInbox
    from substrate.infrastructure.runtime.pg_scheduler import PostgresScheduler
    from substrate.infrastructure.runtime.pg_signal_bus import PostgresSignalBus

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS ravi_run_tree (
    run_id      TEXT NOT NULL PRIMARY KEY,
    parent_run  TEXT,
    root_run    TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    error       TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    supervision JSONB
);
CREATE INDEX IF NOT EXISTS ravi_run_tree_parent_idx ON ravi_run_tree (parent_run);
CREATE INDEX IF NOT EXISTS ravi_run_tree_root_idx ON ravi_run_tree (root_run);

CREATE TABLE IF NOT EXISTS ravi_spawn_effects (
    effect_id    TEXT NOT NULL PRIMARY KEY,
    child_run_id TEXT NOT NULL
);
"""

# Additive migration, mirroring PostgresScheduler's _MIGRATE_COLUMNS — a
# no-op on a fresh table (already covered by _CREATE_TABLES above), needed
# only for a pre-existing deployment's table.
_MIGRATE_COLUMNS: list[tuple[str, str]] = [
    ("supervision", "JSONB"),
]

_STATUS_STR: dict[RunStatus, str] = {
    RunStatus.COMPLETED: "completed",
    RunStatus.FAILED: "failed",
    RunStatus.CANCELLED: "cancelled",
}


class PostgresSupervisor:
    """Postgres-backed Supervisor implementing the kernel Supervisor Protocol."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        event_log: PostgresEventLog,
        inbox: PostgresInbox,
        scheduler: PostgresScheduler,
        signal_bus: PostgresSignalBus,
    ) -> None:
        self._pool = pool
        self._event_log = event_log
        self._inbox = inbox
        self._scheduler = scheduler
        self._signal_bus = signal_bus

    async def setup(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_TABLES)
            for col, defn in _MIGRATE_COLUMNS:
                await conn.execute(
                    f"ALTER TABLE ravi_run_tree ADD COLUMN IF NOT EXISTS {col} {defn}"
                )

    async def spawn(
        self,
        child_agent: AgentId,
        *,
        parent: RunId,
        supervision: Supervision,
        boot: Message,
        path: str,
        correlation_id: str,
    ) -> RunHandle:
        # effect_id derives ONLY from the caller's replay-stable path — see
        # InMemorySupervisor.spawn() and the kernel Supervisor.spawn()
        # docstring for why (never boot.id, never a freshly-computed seq).
        effect_id = Effect.make_id(
            parent, path, "spawn", {"child_agent": str(child_agent)}
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT child_run_id FROM ravi_spawn_effects WHERE effect_id = $1",
                effect_id,
            )
            if row is not None:
                child_run_id: RunId = RunId(row["child_run_id"])
            else:
                child_run_id = new_run_id()
                await conn.execute(
                    """
                    INSERT INTO ravi_spawn_effects (effect_id, child_run_id)
                    VALUES ($1, $2)
                    """,
                    effect_id,
                    child_run_id,
                )
                import json

                await conn.execute(
                    """
                    INSERT INTO ravi_run_tree (run_id, parent_run, root_run, agent_id, status, supervision)
                    VALUES ($1, $2, $3, $4, 'pending', $5::jsonb)
                    """,
                    child_run_id,
                    parent,
                    supervision.run_id,
                    str(child_agent),
                    json.dumps(supervision.to_dict()),
                )
                # Deliver stamped with the caller's replay-stable
                # correlation_id — see RunHandle.boot_correlation_id
                # docstring for why ctx.ask() must never re-deliver.
                boot_with_reply = boot.model_copy(
                    update={"reply_to": parent, "correlation_id": correlation_id}
                )
                await self._inbox.deliver(child_agent, boot_with_reply, notify=False)
                self._scheduler.register_run(child_run_id, child_agent)
                await self._scheduler.enqueue(
                    child_run_id, priority=5, tenant="default"
                )

                # Log spawn in the parent's own EventLog — ONLY on a genuine
                # new spawn; logging unconditionally would duplicate this
                # entry on every replay.
                seq = await self._event_log.last_seq(parent)
                await self._event_log.append(
                    parent,
                    RunLogEntry(
                        run_id=parent,
                        seq=seq + 1,
                        kind="child.spawned",
                        payload={
                            "child_run_id": child_run_id,
                            "child_agent": str(child_agent),
                        },
                    ),
                    expected_seq=seq,
                )

        return RunHandle(
            run_id=child_run_id,
            agent_id=child_agent,
            parent_run=parent,
            boot_correlation_id=correlation_id,
        )

    async def finish_run(
        self, run_id: RunId, status: RunStatus, *, error: str | None = None
    ) -> None:
        """Mark a run terminal and wake its parent (if any) via a signal.

        Called by the Worker on COMPLETED/FAILED/CANCELLED — never on
        SUSPENDED (a suspension is dormancy, not a terminal state; the run
        tree entry stays 'pending' and no signal fires).
        """
        status_str = _STATUS_STR[status]
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                UPDATE ravi_run_tree
                SET status = $1, error = $2
                WHERE run_id = $3
                RETURNING parent_run
                """,
                status_str,
                error,
                run_id,
            )
            # Signal GC: run_id is terminal, so it will never call consume()
            # again — any row still addressed to it (consumed or a stale
            # unconsumed buffer, e.g. a late ask() reply nobody's waiting on
            # anymore) is now permanently dead weight. Regardless of whether
            # this run was ever in ravi_run_tree (a top-level submit() run
            # never is, but can still have accumulated signals as an ask()
            # target or sleep_until_signal() waiter).
            await conn.execute("DELETE FROM ravi_signals WHERE run_id = $1", run_id)
        if row is None:
            return  # this run was never spawned via ctx.spawn() (a top-level submit())
        parent_run = row["parent_run"]
        if parent_run is not None:
            await self._signal_bus.signal(
                RunId(parent_run),
                f"child:{run_id}",
                {"status": status_str, "error": error},
            )

    async def cancel(self, handle: RunHandle, *, reason: str = "cancelled") -> None:
        """Cascade cancellation to ``handle``'s entire subtree, durably.

        Two cases, since a cancelled run may be in either state when this is
        called:

        - **pending/running**: sets ``cancel_requested`` on the queue row.
          A live task only observes this at its next heartbeat (see
          ``PostgresScheduler.heartbeat`` — this is the ≤15s latency the
          plan accepts, tightened by ``ctx.check()`` calls elsewhere in the
          agent loop) and self-terminates via ``CancellationError``, which
          the Worker turns into ``finish_run(CANCELLED)`` — that's what
          actually flips ``ravi_run_tree`` and signals the parent.
        - **suspended**: no live task is polling anything — nothing will
          ever heartbeat it again — so it's terminal-marked directly, right
          here, via the same ``finish_run`` path.
        """
        async with self._pool.acquire() as conn:
            subtree_rows = await conn.fetch(
                """
                WITH RECURSIVE subtree(run_id) AS (
                    -- Seed with the handle's own run_id unconditionally —
                    -- it may be a top-level submit() run with no
                    -- ravi_run_tree row at all (only ctx.spawn()'d runs get
                    -- one), and cancelling it must still cascade to its
                    -- spawned children.
                    SELECT $1::text
                    UNION ALL
                    SELECT rt.run_id
                    FROM ravi_run_tree rt
                    JOIN subtree s ON rt.parent_run = s.run_id
                )
                SELECT run_id FROM subtree
                """,
                handle.run_id,
            )
            subtree_ids = [row["run_id"] for row in subtree_rows]
            if not subtree_ids:
                return
            await conn.execute(
                """
                UPDATE ravi_run_queue
                SET cancel_requested = true
                WHERE run_id = ANY($1) AND status IN ('pending', 'running')
                """,
                subtree_ids,
            )
            suspended_rows = await conn.fetch(
                """
                UPDATE ravi_run_queue
                SET status = 'cancelled', worker_id = NULL, expires_at = NULL,
                    wake_signals = NULL, wake_at = NULL, terminated_at = now()
                WHERE run_id = ANY($1) AND status = 'suspended'
                RETURNING run_id
                """,
                subtree_ids,
            )
        for row in suspended_rows:
            await self.finish_run(
                RunId(row["run_id"]), RunStatus.CANCELLED, error=reason
            )

    def children_of(self, parent: RunId) -> AsyncIterator[RunHandle]:
        return self._children_iter(parent)

    async def _children_iter(self, parent: RunId) -> AsyncIterator[RunHandle]:  # type: ignore[return]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT run_id, agent_id FROM ravi_run_tree WHERE parent_run = $1",
                parent,
            )
        for row in rows:
            type_, _, key = row["agent_id"].partition("/")
            yield RunHandle(
                run_id=RunId(row["run_id"]),
                agent_id=AgentId(type=type_, key=key),
                parent_run=parent,
            )

    async def join(self, handle: RunHandle) -> RunResult:
        """Protocol conformance only — ``RunContext.join()`` never calls this.

        The actual suspend-based join lives in ``agents/runtime/context.py``
        (consumes a ``child:{run_id}`` signal, raising ``SuspendInterrupt`` on
        a miss). Nothing in this codebase calls ``Supervisor.join()``
        directly; this is a lightweight fallback for Protocol conformance.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT status, error FROM ravi_run_tree WHERE run_id = $1",
                handle.run_id,
            )
        if row is None:
            return RunResult(run_id=handle.run_id, status=RunStatus.PENDING)
        status = {
            "completed": RunStatus.COMPLETED,
            "failed": RunStatus.FAILED,
            "cancelled": RunStatus.CANCELLED,
        }.get(row["status"], RunStatus.PENDING)
        return RunResult(run_id=handle.run_id, status=status, error=row["error"])

    async def supervision_of(self, run_id: RunId) -> Supervision | None:
        import json

        async with self._pool.acquire() as conn:
            raw = await conn.fetchval(
                "SELECT supervision FROM ravi_run_tree WHERE run_id = $1", run_id
            )
        if raw is None:
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        return Supervision.from_dict(data)


__all__ = ["PostgresSupervisor"]
