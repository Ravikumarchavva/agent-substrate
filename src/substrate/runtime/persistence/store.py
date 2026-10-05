"""DurableRuntimeStore — the runtime store, written once, over the store's relational database.

The engine owns every rule (what a terminal transition does, how a suspension avoids losing a wakeup, how a
spawn is budgeted); the database owns *atomicity*. Written per database, those rules were duplicated three
times and drifted. Here they are written once, in SQL both SQLite and PostgreSQL accept, over the ``Database``
of ``substrate.stores.database`` — which is also what threads, memory and tasks live in, so one transaction
can span them. Its tables are the runtime's, ``rt_*``, created and versioned by ``migrate``.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from typing import Any, TypeVar

from substrate.types.supervision import Priority, Supervision
from substrate.types.error_info import ErrorInfo
from substrate.types.identity import Actor, Topic
from substrate.types.trace import TraceContext
from substrate.types.errors import BudgetExhaustedError, LeaseLostError, ThreadBusyError
from substrate.types.ids import new_id
from substrate.runtime.message import Message
from substrate.types.run_status import RunId, RunStatus
from substrate.runtime.inbox import DeadLetterEntry, DeadLetterReason
from substrate.types.run_log import RunLogEntry, RunLogKind
from substrate.runtime.scheduler import RunRetryPolicy
from substrate.runtime.persistence.accounts import Accounts
from substrate.runtime.persistence.channels import _SCHEMA_CHANNELS, Channels, _schema_dedup
from substrate.runtime.store import (
    Cancel,
    Commit,
    CommitResult,
    Complete,
    DeliverResult,
    Delivery,
    Fail,
    HeartbeatResult,
    Lease,
    NewEntry,
    Retry,
    RunRecord,
    RunSpec,
    SpawnSpec,
    Spend,
    StoreStats,
    Suspend,
)
from substrate.runtime.supervisor import RunHandle, RunResult
from substrate.stores.database import Database, Row, Tx, migrate
from substrate.types.wakeup import Wakeup

T = TypeVar("T")

_ACTIVE = ("pending", "running", "suspended")
_TERMINAL = ("completed", "failed", "cancelled")
# How long live (ephemeral) entries outlive their run, so a late tail still sees them.
_EPHEMERAL_GRACE_S = 120.0
_TX_ATTEMPTS = 4


_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS rt_runs (
    run_id TEXT PRIMARY KEY,
    agent TEXT NOT NULL,
    tenant TEXT NOT NULL,
    thread_id TEXT,
    parent_run_id TEXT,
    tree_id TEXT NOT NULL,
    status TEXT NOT NULL,
    priority INTEGER NOT NULL,
    epoch INTEGER NOT NULL DEFAULT 0,
    attempt INTEGER NOT NULL DEFAULT 0,
    retry_count INTEGER NOT NULL DEFAULT 0,
    retry_policy TEXT NOT NULL,
    worker_id TEXT,
    lease_expires_at DOUBLE PRECISION,
    wake_json TEXT,
    wake_at DOUBLE PRECISION,
    deadline DOUBLE PRECISION,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    supervision_json TEXT,
    trace TEXT,
    recipe_json TEXT,
    enqueued_at DOUBLE PRECISION NOT NULL,
    started_at DOUBLE PRECISION,
    terminated_at DOUBLE PRECISION,
    result_json TEXT
);
CREATE INDEX IF NOT EXISTS rt_runs_claim_idx ON rt_runs (priority DESC, enqueued_at) WHERE status = 'pending';
CREATE INDEX IF NOT EXISTS rt_runs_timer_idx ON rt_runs (wake_at) WHERE status = 'suspended';
CREATE INDEX IF NOT EXISTS rt_runs_lease_idx ON rt_runs (lease_expires_at) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS rt_runs_agent_idx ON rt_runs (agent, status);
CREATE INDEX IF NOT EXISTS rt_runs_parent_idx ON rt_runs (parent_run_id);
CREATE INDEX IF NOT EXISTS rt_runs_tree_idx ON rt_runs (tree_id, status);
CREATE INDEX IF NOT EXISTS rt_runs_tenant_idx ON rt_runs (tenant);
CREATE UNIQUE INDEX IF NOT EXISTS rt_runs_thread_single_flight
    ON rt_runs (thread_id) WHERE thread_id IS NOT NULL AND status IN ('pending', 'running', 'suspended');

CREATE TABLE IF NOT EXISTS rt_run_wake (
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY (run_id, name)
);
CREATE INDEX IF NOT EXISTS rt_run_wake_name_idx ON rt_run_wake (name);

CREATE TABLE IF NOT EXISTS rt_events (
    run_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    kind TEXT NOT NULL,
    entry_json TEXT NOT NULL,
    ephemeral INTEGER NOT NULL DEFAULT 0,
    dedup_key TEXT,
    PRIMARY KEY (run_id, seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS rt_events_dedup_idx ON rt_events (run_id, dedup_key) WHERE dedup_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS rt_inbox (
    id {pk},
    agent TEXT NOT NULL,
    msg_id TEXT NOT NULL,
    sender TEXT NOT NULL,
    tenant TEXT NOT NULL,
    msg_json TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    UNIQUE (agent, msg_id)
);
CREATE INDEX IF NOT EXISTS rt_inbox_order_idx ON rt_inbox (agent, sender, id);

CREATE TABLE IF NOT EXISTS rt_inbox_processed (
    agent TEXT NOT NULL,
    msg_id TEXT NOT NULL,
    at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (agent, msg_id)
);

CREATE TABLE IF NOT EXISTS rt_dead_letters (
    id {pk},
    agent TEXT NOT NULL,
    msg_id TEXT NOT NULL,
    tenant TEXT NOT NULL,
    entry_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rt_dead_letters_agent_idx ON rt_dead_letters (agent);

CREATE TABLE IF NOT EXISTS rt_signals (
    id {pk},
    run_id TEXT NOT NULL,
    name TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS rt_signals_idx ON rt_signals (run_id, name, id);

CREATE TABLE IF NOT EXISTS rt_signal_claims (
    claim_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rt_spawns (
    effect_id TEXT PRIMARY KEY,
    child_run_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rt_spend (
    tree_id TEXT PRIMARY KEY,
    tokens BIGINT NOT NULL DEFAULT 0,
    cost_micros BIGINT NOT NULL DEFAULT 0,
    turns BIGINT NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS rt_edges (
    topic TEXT NOT NULL,
    follower TEXT NOT NULL,
    PRIMARY KEY (topic, follower)
);
"""


def _ts(dt: datetime) -> float:
    return dt.timestamp()


def _dt(ts: float | None) -> datetime | None:
    return datetime.fromtimestamp(ts, tz=timezone.utc) if ts is not None else None


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


class DurableRuntimeStore(Channels, Accounts):
    """``RuntimeStore`` over a ``Database``."""

    def __init__(
        self,
        database: Database,
        *,
        clock: Callable[[], datetime] = _utcnow,
        max_delivery_attempts: int = 3,
    ) -> None:
        self._db = database
        self._clock = clock
        self._max_attempts = max_delivery_attempts
        self._appended: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        await migrate(
            self._db, "runtime", [_SCHEMA_V1, _SCHEMA_CHANNELS, _schema_dedup]
        )

    async def aclose(self) -> None:
        """The database belongs to whoever opened it (the ``Store``), not to the runtime that uses it."""

    async def _tx(self, fn: Callable[[Tx], Any]) -> Any:
        """Run ``fn`` in one transaction, retrying the whole of it if the database
        aborts it for a reason a retry fixes (a deadlock between two runs)."""
        for attempt in range(_TX_ATTEMPTS):
            try:
                async with self._db.transaction() as tx:
                    return await fn(tx)
            except Exception as exc:  # noqa: BLE001
                if attempt + 1 < _TX_ATTEMPTS and self._db.is_retryable(exc):
                    await asyncio.sleep(0.01 * (attempt + 1))
                    continue
                raise

    def _notify(self, run_ids: set[str]) -> None:
        for run_id in run_ids:
            event = self._appended.get(run_id)
            if event is not None:
                event.set()

    # ------------------------------------------------------------------ records

    def _record(self, row: Row) -> RunRecord:
        return RunRecord(
            run_id=RunId(row["run_id"]),
            agent=Actor.from_str(row["agent"]),
            tenant=row["tenant"],
            status=RunStatus(row["status"]),
            thread_id=row["thread_id"],
            parent_run_id=RunId(row["parent_run_id"]) if row["parent_run_id"] else None,
            tree_id=row["tree_id"],
            priority=Priority(row["priority"]),
            epoch=row["epoch"],
            attempt=row["attempt"],
            retry_count=row["retry_count"],
            retry_policy=RunRetryPolicy.model_validate_json(row["retry_policy"]),
            worker_id=row["worker_id"],
            lease_expires_at=_dt(row["lease_expires_at"]),
            wake=Wakeup.model_validate_json(row["wake_json"])
            if row["wake_json"]
            else None,
            wake_at=_dt(row["wake_at"]),
            deadline=_dt(row["deadline"]),
            cancel_requested=bool(row["cancel_requested"]),
            supervision=Supervision.from_dict(json.loads(row["supervision_json"]))
            if row["supervision_json"]
            else None,
            trace=TraceContext.from_traceparent(row["trace"]) if row["trace"] else None,
            enqueued_at=_dt(row["enqueued_at"]),
            started_at=_dt(row["started_at"]),
            terminated_at=_dt(row["terminated_at"]),
            result=RunResult.model_validate_json(row["result_json"])
            if row["result_json"]
            else None,
            recipe=json.loads(row["recipe_json"]) if row["recipe_json"] else None,
        )

    def _lease(self, row: Row, worker_id: str) -> Lease:
        record = self._record(row)
        return Lease(
            run_id=record.run_id,
            agent=record.agent,
            worker_id=worker_id,
            epoch=record.epoch,
            expires_at=record.lease_expires_at or self._clock(),
            attempt=record.attempt,
            tenant=record.tenant,
            thread_id=record.thread_id,
            retry_count=record.retry_count,
            retry_policy=record.retry_policy,
            supervision=record.supervision,
            trace=record.trace,
            deadline=record.deadline,
            started_at=record.started_at,
            parent_run_id=record.parent_run_id,
        )

    # ------------------------------------------------------------------ helpers (inside a transaction)

    async def _append(self, tx: Tx, run_id: str, entry: NewEntry) -> int:
        """Append one entry and return its seq. A repeated ``dedup_key`` returns the seq
        already there and writes nothing."""
        await tx.lock(run_id)
        if entry.dedup_key is not None:
            existing = await tx.fetchone(
                "SELECT seq FROM rt_events WHERE run_id = ? AND dedup_key = ?",
                run_id,
                entry.dedup_key,
            )
            if existing is not None:
                return int(existing["seq"])
        row = await tx.fetchone(
            "SELECT COALESCE(MAX(seq), -1) AS m FROM rt_events WHERE run_id = ?", run_id
        )
        seq = int(row["m"]) + 1  # type: ignore[index]
        log = RunLogEntry(
            run_id=RunId(run_id),
            seq=seq,
            kind=entry.kind,
            payload=entry.payload,
            ts=self._clock(),
        )
        await tx.execute(
            "INSERT INTO rt_events (run_id, seq, kind, entry_json, ephemeral, dedup_key) VALUES (?, ?, ?, ?, ?, ?)",
            run_id,
            seq,
            entry.kind,
            log.model_dump_json(),
            int(entry.ephemeral),
            entry.dedup_key,
        )
        if entry.spend is not None:
            await tx.execute(
                "INSERT INTO rt_spend (tree_id, tokens, cost_micros, turns) "
                "SELECT tree_id, ?, ?, ? FROM rt_runs WHERE run_id = ? "
                "ON CONFLICT (tree_id) DO UPDATE SET tokens = rt_spend.tokens + EXCLUDED.tokens, "
                "cost_micros = rt_spend.cost_micros + EXCLUDED.cost_micros, turns = rt_spend.turns + EXCLUDED.turns",
                entry.spend.tokens,
                round(entry.spend.cost_usd * 1_000_000),
                entry.spend.turns,
                run_id,
            )
            await tx.execute(
                "INSERT INTO rt_accounts (account, tokens, cost_micros, turns) "
                "SELECT account, ?, ?, ? FROM rt_run_accounts WHERE run_id = ? "
                "ON CONFLICT (account) DO UPDATE SET tokens = rt_accounts.tokens + EXCLUDED.tokens, "
                "cost_micros = rt_accounts.cost_micros + EXCLUDED.cost_micros, turns = rt_accounts.turns + EXCLUDED.turns",
                entry.spend.tokens,
                round(entry.spend.cost_usd * 1_000_000),
                entry.spend.turns,
                run_id,
            )
        return seq

    async def _make_pending(self, tx: Tx, run_id: str) -> None:
        await tx.execute(
            "UPDATE rt_runs SET status = 'pending', worker_id = NULL, lease_expires_at = NULL, wake_at = NULL, "
            "wake_json = NULL WHERE run_id = ? AND status = 'suspended'",
            run_id,
        )
        await tx.execute("DELETE FROM rt_run_wake WHERE run_id = ?", run_id)

    async def _signal(
        self, tx: Tx, run_id: str, name: str, payload: dict[str, Any]
    ) -> bool:
        """Buffer a signal, and wake the run if it is suspended waiting on ``name``."""
        await tx.lock(run_id)
        run = await tx.fetchone("SELECT status FROM rt_runs WHERE run_id = ?", run_id)
        if run is not None and run["status"] in _TERMINAL:
            return False  # nobody will ever read it
        # An unknown run id is buffered, not dropped: the receiver may be an observer that
        # polls for its answer without being a run (``Runtime.ask``).
        await tx.execute(
            "INSERT INTO rt_signals (run_id, name, payload_json) VALUES (?, ?, ?)",
            run_id,
            name,
            json.dumps(payload),
        )
        if run is None or run["status"] != "suspended":
            return False
        waiting = await tx.fetchone(
            "SELECT 1 AS x FROM rt_run_wake WHERE run_id = ? AND name = ?", run_id, name
        )
        if waiting is None:
            return False
        await self._make_pending(tx, run_id)
        return True

    async def _insert_run(self, tx: Tx, spec: RunSpec) -> Row:
        run_id = spec.run_id or RunId(new_id())
        tree_id = spec.supervision.run_id if spec.supervision else run_id
        try:
            await tx.execute(
                "INSERT INTO rt_runs (run_id, agent, tenant, thread_id, parent_run_id, tree_id, status, priority, "
                "retry_policy, deadline, supervision_json, trace, recipe_json, enqueued_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?)",
                run_id,
                str(spec.agent),
                spec.tenant,
                spec.thread_id,
                spec.parent_run_id,
                tree_id,
                int(spec.priority),
                spec.retry_policy.model_dump_json(),
                _ts(spec.deadline) if spec.deadline else None,
                json.dumps(spec.supervision.to_dict()) if spec.supervision else None,
                spec.trace.to_traceparent() if spec.trace else None,
                json.dumps(spec.recipe) if spec.recipe is not None else None,
                _ts(self._clock()),
            )
            for account in spec.accounts:
                await tx.execute(
                    "INSERT INTO rt_run_accounts (run_id, account) VALUES (?, ?) ON CONFLICT DO NOTHING",
                    run_id,
                    account,
                )
        except Exception as exc:
            if spec.thread_id is not None and self._db.is_unique_violation(exc):
                raise ThreadBusyError(
                    f"thread {spec.thread_id} already has an active run",
                    thread_id=spec.thread_id,
                ) from exc
            raise
        row = await tx.fetchone("SELECT * FROM rt_runs WHERE run_id = ?", run_id)
        assert row is not None
        return row

    async def _deliver(
        self, tx: Tx, delivery: Delivery, *, wake: bool
    ) -> DeliverResult:
        agent = str(delivery.agent)
        msg = delivery.msg
        if await tx.fetchone(
            "SELECT 1 AS x FROM rt_inbox WHERE agent = ? AND msg_id = ?", agent, msg.id
        ):
            return DeliverResult(accepted=False)
        if await tx.fetchone(
            "SELECT 1 AS x FROM rt_inbox_processed WHERE agent = ? AND msg_id = ?",
            agent,
            msg.id,
        ):
            return DeliverResult(accepted=False)
        await tx.execute(
            "INSERT INTO rt_inbox (agent, msg_id, sender, tenant, msg_json) VALUES (?, ?, ?, ?, ?)",
            agent,
            msg.id,
            str(msg.sender),
            delivery.tenant,
            msg.model_dump_json(),
        )
        if not wake:
            return DeliverResult(accepted=True)
        # Without this, two deliveries to an idle actor each see "no active run" and both
        # insert one; both then drain the same inbox.
        await tx.lock(f"agent:{agent}")
        active = await tx.fetchone(
            "SELECT run_id, status FROM rt_runs WHERE agent = ? AND status IN ('pending', 'running', 'suspended') "
            "ORDER BY enqueued_at DESC, run_id DESC LIMIT 1",
            agent,
        )
        if active is None:
            row = await self._insert_run(
                tx,
                RunSpec(
                    agent=delivery.agent,
                    tenant=delivery.tenant,
                    trace=TraceContext.new(),
                    accounts=delivery.accounts,
                ),
            )
            return DeliverResult(accepted=True, created_run=RunId(row["run_id"]))
        if active["status"] == "suspended":
            await tx.lock(active["run_id"])
            await self._make_pending(tx, active["run_id"])
            return DeliverResult(accepted=True, woke_run=RunId(active["run_id"]))
        return DeliverResult(accepted=True)

    async def _ack(self, tx: Tx, agent: str, msg_ids: Sequence[str]) -> None:
        now = _ts(self._clock())
        for msg_id in msg_ids:
            await tx.execute(
                "DELETE FROM rt_inbox WHERE agent = ? AND msg_id = ?", agent, msg_id
            )
            await tx.execute(
                "INSERT INTO rt_inbox_processed (agent, msg_id, at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
                agent,
                msg_id,
                now,
            )

    async def _nack(
        self, tx: Tx, agent: str, msg_id: str, error: ErrorInfo, *, final: bool = False
    ) -> None:
        row = await tx.fetchone(
            "SELECT msg_json, attempts, tenant FROM rt_inbox WHERE agent = ? AND msg_id = ?",
            agent,
            msg_id,
        )
        if row is None:
            return
        attempts = int(row["attempts"]) + 1
        if attempts < self._max_attempts and not final:
            await tx.execute(
                "UPDATE rt_inbox SET attempts = ? WHERE agent = ? AND msg_id = ?",
                attempts,
                agent,
                msg_id,
            )
            return
        entry = DeadLetterEntry(
            agent_id=Actor.from_str(agent),
            msg=Message.model_validate_json(row["msg_json"]),
            reason=DeadLetterReason.MAX_RETRIES,
            attempts=attempts,
            last_error=str(error),
        )
        await tx.execute(
            "INSERT INTO rt_dead_letters (agent, msg_id, tenant, entry_json) VALUES (?, ?, ?, ?)",
            agent,
            msg_id,
            row["tenant"],
            entry.model_dump_json(),
        )
        # Kept in the processed set so a transport redelivering it is not accepted again.
        await self._ack(tx, agent, [msg_id])

    async def _terminate(
        self, tx: Tx, run: Row, outcome: Complete | Fail | Cancel
    ) -> None:
        """Move a run to a terminal state: status, journal entry, result, the signal that
        wakes its parent, and the fate of its children — one unit."""
        run_id = run["run_id"]
        now = _ts(self._clock())
        status = {
            "complete": RunStatus.COMPLETED,
            "fail": RunStatus.FAILED,
            "cancel": RunStatus.CANCELLED,
        }[outcome.kind]
        error: ErrorInfo | None = None
        if isinstance(outcome, Complete):
            kind, payload = RunLogKind.RUN_COMPLETED, {}
            result = RunResult(run_id=RunId(run_id), status=status)
        elif isinstance(outcome, Fail):
            kind = RunLogKind.RUN_FAILED
            error = outcome.error
            payload = {
                "error": error.message,
                "status": error.code,
                "error_info": error.model_dump(mode="json"),
            }
            result = RunResult(run_id=RunId(run_id), status=status, error=str(error))
        else:
            kind, payload = RunLogKind.RUN_CANCELLED, {"reason": outcome.reason}
            result = RunResult(run_id=RunId(run_id), status=status)
        # One shared dedup key across the terminal kinds makes a second terminal entry for
        # the same run impossible, not merely unlikely.
        await self._append(
            tx, run_id, NewEntry(kind=kind, payload=payload, dedup_key="terminal")
        )
        await tx.execute(
            "UPDATE rt_runs SET status = ?, worker_id = NULL, lease_expires_at = NULL, wake_at = NULL, wake_json = NULL, "
            "terminated_at = ?, result_json = ? WHERE run_id = ?",
            status.value,
            now,
            result.model_dump_json(),
            run_id,
        )
        await tx.execute("DELETE FROM rt_run_wake WHERE run_id = ?", run_id)
        await tx.execute("DELETE FROM rt_signals WHERE run_id = ?", run_id)
        if run["parent_run_id"]:
            how = {
                "fail": "target_failed",
                "cancel": "target_cancelled",
                "complete": "completed",
            }[outcome.kind]
            await self._signal(
                tx,
                run["parent_run_id"],
                f"child:{run_id}",
                {
                    "status": status.value,
                    "kind": how,
                    "error": error.model_dump(mode="json") if error else None,
                },
            )
        # Children of a run that did not finish cannot report to anyone; stop them rather than
        # leave them running and spending.
        if outcome.kind != "complete":
            for child in await tx.fetchall(
                "SELECT run_id FROM rt_runs WHERE parent_run_id = ? AND status IN ('pending', 'running', 'suspended')",
                run_id,
            ):
                await self._cancel_one(
                    tx, child["run_id"], reason=f"parent {run_id} {outcome.kind}"
                )
        # Mail that arrived while this run was finishing has nobody to read it.
        if outcome.kind == "complete":
            await tx.lock(f"agent:{run['agent']}")
            leftover = await tx.fetchone(
                "SELECT 1 AS x FROM rt_inbox WHERE agent = ? LIMIT 1", run["agent"]
            )
            other = await tx.fetchone(
                "SELECT 1 AS x FROM rt_runs WHERE agent = ? AND status IN ('pending', 'running', 'suspended') AND run_id != ?",
                run["agent"],
                run_id,
            )
            if leftover and not other:
                trace = (
                    TraceContext.from_traceparent(run["trace"]).child()
                    if run["trace"]
                    else TraceContext.new()
                )
                await self._insert_run(
                    tx,
                    RunSpec(
                        agent=Actor.from_str(run["agent"]),
                        tenant=run["tenant"],
                        trace=trace,
                    ),
                )

    async def _cancel_one(self, tx: Tx, run_id: str, *, reason: str) -> bool:
        await tx.lock(run_id)
        run = await tx.fetchone("SELECT * FROM rt_runs WHERE run_id = ?", run_id)
        if run is None or run["status"] in _TERMINAL:
            return False
        if run["status"] == "running":
            await tx.execute(
                "UPDATE rt_runs SET cancel_requested = 1 WHERE run_id = ?", run_id
            )
            return True
        await self._terminate(tx, run, Cancel(reason=reason))
        return True

    # ------------------------------------------------------------------ runs

    async def create_run(
        self, spec: RunSpec, *, deliveries: Sequence[Delivery] = ()
    ) -> RunRecord:
        async def do(tx: Tx) -> RunRecord:
            row = await self._insert_run(tx, spec)
            for delivery in deliveries:
                await self._deliver(tx, delivery, wake=False)
            return self._record(row)

        return await self._tx(do)

    async def lease(
        self, *, worker_id: str, capacity: int, lease_s: float, now: datetime
    ) -> list[Lease]:
        now_ts = _ts(now)
        expires = now_ts + lease_s

        async def do(tx: Tx) -> list[Lease]:
            await tx.lock("lease")  # one claimer at a time; leasing is short
            # A lease that expired means its worker is presumed dead.
            await tx.execute(
                "UPDATE rt_runs SET status = 'pending', worker_id = NULL, lease_expires_at = NULL "
                "WHERE status = 'running' AND lease_expires_at < ?",
                now_ts,
            )
            for row in await tx.fetchall(
                "SELECT run_id FROM rt_runs WHERE status = 'suspended' AND wake_at IS NOT NULL AND wake_at <= ?",
                now_ts,
            ):
                await tx.lock(row["run_id"])
                await self._make_pending(tx, row["run_id"])
            # A run past its deadline ends now, even if nobody is working on it.
            for row in await tx.fetchall(
                "SELECT * FROM rt_runs WHERE status IN ('pending', 'suspended') AND deadline IS NOT NULL AND deadline <= ?",
                now_ts,
            ):
                await tx.lock(row["run_id"])
                await self._terminate(
                    tx,
                    row,
                    Fail(
                        error=ErrorInfo(
                            code="deadline_exceeded",
                            message="the run's deadline passed",
                            retryable=False,
                        )
                    ),
                )
            # Live output is kept briefly after its run ends, then dropped.
            await tx.execute(
                "DELETE FROM rt_events WHERE ephemeral = 1 AND run_id IN "
                "(SELECT run_id FROM rt_runs WHERE terminated_at IS NOT NULL AND terminated_at < ?)",
                now_ts - _EPHEMERAL_GRACE_S,
            )
            if capacity <= 0:
                return []
            # Priority first, then fair across tenants so one tenant cannot starve the rest.
            candidates = await tx.fetchall(
                "SELECT run_id FROM ("
                "  SELECT run_id, priority, enqueued_at, "
                "         ROW_NUMBER() OVER (PARTITION BY tenant ORDER BY priority DESC, enqueued_at, run_id) AS rn "
                "  FROM rt_runs WHERE status = 'pending'"
                ") q ORDER BY rn, priority DESC, enqueued_at, run_id LIMIT ?",
                capacity,
            )
            leases: list[Lease] = []
            for candidate in candidates:
                await tx.execute(
                    "UPDATE rt_runs SET status = 'running', worker_id = ?, lease_expires_at = ?, epoch = epoch + 1, "
                    "attempt = attempt + 1, started_at = COALESCE(started_at, ?) WHERE run_id = ?",
                    worker_id,
                    expires,
                    now_ts,
                    candidate["run_id"],
                )
                row = await tx.fetchone(
                    "SELECT * FROM rt_runs WHERE run_id = ?", candidate["run_id"]
                )
                assert row is not None
                leases.append(self._lease(row, worker_id))
            return leases

        return await self._tx(do)

    async def heartbeat(
        self, lease: Lease, *, lease_s: float, now: datetime
    ) -> HeartbeatResult:
        now_ts = _ts(now)

        async def do(tx: Tx) -> HeartbeatResult:
            await tx.lock(str(lease.run_id))
            row = await tx.fetchone(
                "SELECT cancel_requested, deadline FROM rt_runs "
                "WHERE run_id = ? AND worker_id = ? AND epoch = ? AND status = 'running'",
                lease.run_id,
                lease.worker_id,
                lease.epoch,
            )
            if row is None:
                return HeartbeatResult.LOST
            await tx.execute(
                "UPDATE rt_runs SET lease_expires_at = ? WHERE run_id = ?",
                now_ts + lease_s,
                lease.run_id,
            )
            if row["cancel_requested"]:
                return HeartbeatResult.CANCEL_REQUESTED
            if row["deadline"] is not None and now_ts >= row["deadline"]:
                return HeartbeatResult.DEADLINE
            return HeartbeatResult.OK

        return await self._tx(do)

    async def _fenced_run(self, tx: Tx, lease: Lease) -> Row:
        await tx.lock(str(lease.run_id))
        run = await tx.fetchone("SELECT * FROM rt_runs WHERE run_id = ?", lease.run_id)
        if (
            run is None
            or run["status"] != "running"
            or run["epoch"] != lease.epoch
            or run["worker_id"] != lease.worker_id
        ):
            raise LeaseLostError(str(lease.run_id), epoch=lease.epoch)
        return run

    async def commit(self, lease: Lease, commit: Commit) -> CommitResult:
        touched: set[str] = {str(lease.run_id)}

        async def do(tx: Tx) -> CommitResult:
            run = await self._fenced_run(tx, lease)
            run_id = run["run_id"]
            seqs = [await self._append(tx, run_id, entry) for entry in commit.entries]
            agent = run["agent"]
            await self._ack(tx, agent, commit.ack)
            for nack in commit.nack:
                await self._nack(tx, agent, nack.msg_id, nack.error, final=nack.final)

            spawned: dict[str, RunHandle] = {}
            for spawn in commit.spawns:
                handle = await self._spawn(tx, run, spawn)
                spawned[spawn.effect_id] = handle
                seqs.append(
                    await self._append(
                        tx,
                        run_id,
                        NewEntry(
                            kind=RunLogKind.CHILD_SPAWNED,
                            payload={
                                "child_run_id": str(handle.run_id),
                                "child_agent": str(handle.agent_id),
                            },
                            dedup_key=f"spawn:{spawn.effect_id}",
                        ),
                    )
                )
                touched.add(str(handle.run_id))

            results = tuple(
                [await self._deliver(tx, d, wake=True) for d in commit.deliveries]
            )
            for signal in commit.signals:
                await self._signal(tx, str(signal.run_id), signal.name, signal.payload)
                touched.add(str(signal.run_id))

            status = RunStatus(run["status"])
            resumed = False
            outcome = commit.outcome
            if (
                outcome is not None
                and run["cancel_requested"]
                and not isinstance(outcome, (Complete, Fail, Cancel))
            ):
                # A cancel arrived while this attempt was working: honour it rather than sleep or retry.
                outcome = Cancel(reason="cancelled")
            if isinstance(outcome, Suspend):
                status, resumed = await self._suspend(tx, run_id, outcome.wake)
                if status == RunStatus.SUSPENDED:
                    seqs.append(
                        await self._append(
                            tx,
                            run_id,
                            NewEntry(
                                kind=RunLogKind.RUN_SUSPENDED,
                                payload={"wake": outcome.wake.model_dump(mode="json")},
                            ),
                        )
                    )
            elif isinstance(outcome, Retry):
                await self._retry(tx, run, outcome)
                status = RunStatus.SUSPENDED
            elif isinstance(outcome, (Complete, Fail, Cancel)):
                await self._terminate(tx, run, outcome)
                status = {
                    "complete": RunStatus.COMPLETED,
                    "fail": RunStatus.FAILED,
                    "cancel": RunStatus.CANCELLED,
                }[outcome.kind]
            return CommitResult(
                seqs=tuple(seqs),
                spawned=spawned,
                deliveries=results,
                resumed_immediately=resumed,
                status=status,
            )

        result = await self._tx(do)
        self._notify(touched)
        return result

    async def _suspend(
        self, tx: Tx, run_id: str, wake: Wakeup
    ) -> tuple[RunStatus, bool]:
        """Park a run — unless what it is waiting for has already arrived.

        The check and the status change are one transaction, serialised with ``_signal`` on
        this run. Without that, a signal landing between "the run found nothing" and "the
        run went to sleep" is buffered for a run that is not yet asleep, wakes nothing, and
        the run sleeps forever with its answer sitting in the buffer.
        """
        names = list(wake.signals or [])
        if wake.kind == "child_done" and wake.child_run:
            names.append(f"child:{wake.child_run}")
        wake_at = _ts(wake.at) if wake.at else None
        await tx.execute(
            "UPDATE rt_runs SET status = 'suspended', worker_id = NULL, lease_expires_at = NULL, wake_json = ?, wake_at = ? "
            "WHERE run_id = ?",
            wake.model_dump_json(),
            wake_at,
            run_id,
        )
        await tx.execute("DELETE FROM rt_run_wake WHERE run_id = ?", run_id)
        for name in names:
            await tx.execute(
                "INSERT INTO rt_run_wake (run_id, name) VALUES (?, ?) ON CONFLICT DO NOTHING",
                run_id,
                name,
            )
        ready = False
        if names:
            marks = ",".join("?" for _ in names)
            ready = (
                await tx.fetchone(
                    f"SELECT 1 AS x FROM rt_signals WHERE run_id = ? AND name IN ({marks}) LIMIT 1",
                    run_id,
                    *names,
                )
            ) is not None
        if not ready and wake_at is not None and wake_at <= _ts(self._clock()):
            ready = True
        if ready:
            await self._make_pending(tx, run_id)
            return RunStatus.PENDING, True
        return RunStatus.SUSPENDED, False

    async def _retry(self, tx: Tx, run: Row, outcome: Retry) -> None:
        run_id = run["run_id"]
        wake_at = _ts(self._clock()) + max(outcome.delay_s, 0.0)
        await tx.execute(
            "UPDATE rt_runs SET status = 'suspended', worker_id = NULL, lease_expires_at = NULL, "
            "retry_count = retry_count + 1, wake_at = ?, wake_json = NULL WHERE run_id = ?",
            wake_at,
            run_id,
        )
        await tx.execute("DELETE FROM rt_run_wake WHERE run_id = ?", run_id)
        await self._append(
            tx,
            run_id,
            NewEntry(
                kind=RunLogKind.RUN_RETRYING,
                payload={
                    "error": outcome.error.model_dump(mode="json"),
                    "delay_s": outcome.delay_s,
                },
            ),
        )

    async def _spawn(self, tx: Tx, parent: Row, spawn: SpawnSpec) -> RunHandle:
        existing = await tx.fetchone(
            "SELECT child_run_id FROM rt_spawns WHERE effect_id = ?", spawn.effect_id
        )
        if existing is not None:
            child_id = existing["child_run_id"]
            child = await tx.fetchone(
                "SELECT agent FROM rt_runs WHERE run_id = ?", child_id
            )
            assert child is not None
            return RunHandle(
                run_id=RunId(child_id),
                agent_id=Actor.from_str(child["agent"]),
                parent_run=RunId(parent["run_id"]),
                boot_correlation_id=spawn.boot.correlation_id,
            )
        supervision = spawn.child.supervision
        if supervision is not None:
            counted = await tx.fetchone(
                "SELECT COUNT(*) AS c FROM rt_runs WHERE tree_id = ? AND status IN ('pending', 'running', 'suspended')",
                supervision.run_id,
            )
            active = int(counted["c"])  # type: ignore[index]
            if active >= supervision.spawn_budget.max_agents:
                raise BudgetExhaustedError(
                    f"run tree {supervision.run_id} is at its headcount cap "
                    f"({active}/{supervision.spawn_budget.max_agents} agents); cannot spawn {spawn.child.agent}"
                )
        inherited = await tx.fetchall(
            "SELECT account FROM rt_run_accounts WHERE run_id = ?", parent["run_id"]
        )
        row = await self._insert_run(
            tx,
            spawn.child.model_copy(
                update={
                    "parent_run_id": RunId(parent["run_id"]),
                    "accounts": tuple(
                        dict.fromkeys(
                            (*spawn.child.accounts, *(r["account"] for r in inherited))
                        )
                    ),
                }
            ),
        )
        await self._deliver(
            tx,
            Delivery(
                agent=spawn.child.agent, msg=spawn.boot, tenant=spawn.child.tenant
            ),
            wake=False,
        )
        await tx.execute(
            "INSERT INTO rt_spawns (effect_id, child_run_id) VALUES (?, ?)",
            spawn.effect_id,
            row["run_id"],
        )
        return RunHandle(
            run_id=RunId(row["run_id"]),
            agent_id=spawn.child.agent,
            parent_run=RunId(parent["run_id"]),
            boot_correlation_id=spawn.boot.correlation_id,
        )

    async def get_run(self, run_id: RunId) -> RunRecord | None:
        async def do(tx: Tx) -> RunRecord | None:
            row = await tx.fetchone("SELECT * FROM rt_runs WHERE run_id = ?", run_id)
            return self._record(row) if row else None

        return await self._tx(do)

    async def find_runs(
        self,
        *,
        thread_id: str | None = None,
        agent: Actor | None = None,
        wake_signal: str | None = None,
        tenant: str | None = None,
        active_only: bool = True,
    ) -> list[RunRecord]:
        async def do(tx: Tx) -> list[RunRecord]:
            where: list[str] = []
            args: list[Any] = []
            if thread_id is not None:
                where.append("thread_id = ?")
                args.append(thread_id)
            if agent is not None:
                where.append("agent = ?")
                args.append(str(agent))
            if tenant is not None:
                where.append("tenant = ?")
                args.append(tenant)
            if wake_signal is not None:
                where.append(
                    "status = 'suspended' AND run_id IN (SELECT run_id FROM rt_run_wake WHERE name = ?)"
                )
                args.append(wake_signal)
            if active_only:
                where.append("status IN ('pending', 'running', 'suspended')")
            sql = (
                "SELECT * FROM rt_runs"
                + (" WHERE " + " AND ".join(where) if where else "")
                + " ORDER BY enqueued_at, run_id"
            )
            return [self._record(r) for r in await tx.fetchall(sql, *args)]

        return await self._tx(do)

    async def children(self, run_id: RunId) -> list[RunRecord]:
        async def do(tx: Tx) -> list[RunRecord]:
            rows = await tx.fetchall(
                "SELECT * FROM rt_runs WHERE parent_run_id = ? ORDER BY enqueued_at, run_id",
                run_id,
            )
            return [self._record(r) for r in rows]

        return await self._tx(do)

    async def request_cancel(
        self, run_id: RunId, *, reason: str, cascade: bool = True
    ) -> list[RunId]:
        async def do(tx: Tx) -> list[RunId]:
            order: list[str] = []
            frontier = [str(run_id)]
            while frontier:
                current = frontier.pop(0)
                order.append(current)
                if cascade:
                    frontier.extend(
                        r["run_id"]
                        for r in await tx.fetchall(
                            "SELECT run_id FROM rt_runs WHERE parent_run_id = ?",
                            current,
                        )
                    )
            affected: list[RunId] = []
            # Children first, so a parent woken by a child's end finds it already gone.
            for rid in reversed(order):
                if await self._cancel_one(tx, rid, reason=reason):
                    affected.append(RunId(rid))
            return affected

        affected = await self._tx(do)
        self._notify({str(r) for r in affected})
        return affected

    # ------------------------------------------------------------------ journal

    async def read_events(
        self,
        run_id: RunId,
        *,
        from_seq: int = 0,
        limit: int | None = None,
        durable_only: bool = False,
    ) -> list[RunLogEntry]:
        async def do(tx: Tx) -> list[RunLogEntry]:
            sql = "SELECT entry_json FROM rt_events WHERE run_id = ? AND seq >= ?"
            if durable_only:
                sql += " AND ephemeral = 0"
            sql += " ORDER BY seq"
            args: list[Any] = [run_id, from_seq]
            if limit is not None:
                sql += " LIMIT ?"
                args.append(limit)
            return [
                RunLogEntry.model_validate_json(r["entry_json"])
                for r in await tx.fetchall(sql, *args)
            ]

        return await self._tx(do)

    async def last_seq(self, run_id: RunId) -> int:
        async def do(tx: Tx) -> int:
            row = await tx.fetchone(
                "SELECT COALESCE(MAX(seq), -1) AS m FROM rt_events WHERE run_id = ?",
                run_id,
            )
            return int(row["m"])  # type: ignore[index]

        return await self._tx(do)

    async def wait_events(
        self, run_id: RunId, *, after_seq: int, timeout_s: float
    ) -> None:
        event = self._appended.setdefault(str(run_id), asyncio.Event())
        event.clear()
        if await self.last_seq(run_id) > after_seq:
            return
        try:
            # A write by another process cannot set this event, so the wait is bounded.
            await asyncio.wait_for(event.wait(), timeout=min(timeout_s, 0.25))
        except asyncio.TimeoutError:
            pass

    async def annotate(self, run_id: RunId, entries: Sequence[NewEntry]) -> list[int]:
        async def do(tx: Tx) -> list[int]:
            await tx.lock(str(run_id))
            if (
                await tx.fetchone("SELECT 1 AS x FROM rt_runs WHERE run_id = ?", run_id)
                is None
            ):
                raise KeyError(run_id)
            return [await self._append(tx, str(run_id), entry) for entry in entries]

        seqs = await self._tx(do)
        self._notify({str(run_id)})
        return seqs

    async def append_ephemeral(self, lease: Lease, entries: Sequence[NewEntry]) -> None:
        async def do(tx: Tx) -> None:
            run = await self._fenced_run(tx, lease)
            for entry in entries:
                await self._append(
                    tx,
                    run["run_id"],
                    entry.model_copy(update={"ephemeral": True, "dedup_key": None}),
                )

        await self._tx(do)
        self._notify({str(lease.run_id)})

    # ------------------------------------------------------------------ inbox

    async def deliver(self, delivery: Delivery, *, wake: bool = True) -> DeliverResult:
        return await self._tx(lambda tx: self._deliver(tx, delivery, wake=wake))

    async def drain(self, agent: Actor, *, limit: int = 100) -> list[Message]:
        async def do(tx: Tx) -> list[Message]:
            rows = await tx.fetchall(
                "SELECT msg_json FROM rt_inbox WHERE agent = ? ORDER BY sender, id LIMIT ?",
                str(agent),
                limit,
            )
            return [Message.model_validate_json(r["msg_json"]) for r in rows]

        return await self._tx(do)

    async def pending_count(self, agent: Actor) -> int:
        async def do(tx: Tx) -> int:
            row = await tx.fetchone(
                "SELECT COUNT(*) AS c FROM rt_inbox WHERE agent = ?", str(agent)
            )
            return int(row["c"])  # type: ignore[index]

        return await self._tx(do)

    async def working(self, agents: Sequence[Actor]) -> list[Actor]:
        if not agents:
            return []

        async def do(tx: Tx) -> list[Actor]:
            marks = ", ".join("?" for _ in agents)
            rows = await tx.fetchall(
                f"SELECT DISTINCT agent FROM rt_runs WHERE status IN ('pending', 'running') AND agent IN ({marks})",
                *[str(a) for a in agents],
            )
            return [Actor.from_str(r["agent"]) for r in rows]

        return await self._tx(do)

    async def dead_letters(self, agent: Actor) -> list[DeadLetterEntry]:
        async def do(tx: Tx) -> list[DeadLetterEntry]:
            rows = await tx.fetchall(
                "SELECT entry_json FROM rt_dead_letters WHERE agent = ? ORDER BY id",
                str(agent),
            )
            return [DeadLetterEntry.model_validate_json(r["entry_json"]) for r in rows]

        return await self._tx(do)

    async def redrive(self, agent: Actor, msg_id: str) -> bool:
        async def do(tx: Tx) -> bool:
            row = await tx.fetchone(
                "SELECT id, entry_json, tenant FROM rt_dead_letters WHERE agent = ? AND msg_id = ?",
                str(agent),
                msg_id,
            )
            if row is None:
                return False
            entry = DeadLetterEntry.model_validate_json(row["entry_json"])
            await tx.execute("DELETE FROM rt_dead_letters WHERE id = ?", row["id"])
            await tx.execute(
                "DELETE FROM rt_inbox_processed WHERE agent = ? AND msg_id = ?",
                str(agent),
                msg_id,
            )
            await self._deliver(
                tx,
                Delivery(agent=agent, msg=entry.msg, tenant=row["tenant"]),
                wake=True,
            )
            return True

        return await self._tx(do)

    # ------------------------------------------------------------------ signals

    async def signal(self, run_id: RunId, name: str, payload: dict[str, Any]) -> bool:
        return await self._tx(lambda tx: self._signal(tx, str(run_id), name, payload))

    async def consume(
        self, run_id: RunId, name: str, claim_id: str
    ) -> dict[str, Any] | None:
        async def do(tx: Tx) -> dict[str, Any] | None:
            await tx.lock(str(run_id))
            claimed = await tx.fetchone(
                "SELECT payload_json FROM rt_signal_claims WHERE claim_id = ?", claim_id
            )
            if claimed is not None:
                return json.loads(claimed["payload_json"])
            row = await tx.fetchone(
                "SELECT id, payload_json FROM rt_signals WHERE run_id = ? AND name = ? ORDER BY id LIMIT 1",
                run_id,
                name,
            )
            if row is None:
                return None
            await tx.execute("DELETE FROM rt_signals WHERE id = ?", row["id"])
            await tx.execute(
                "INSERT INTO rt_signal_claims (claim_id, payload_json) VALUES (?, ?)",
                claim_id,
                row["payload_json"],
            )
            return json.loads(row["payload_json"])

        return await self._tx(do)

    # ------------------------------------------------------------------ follow graph

    async def follow(self, follower: Actor, topic: Topic) -> None:
        async def do(tx: Tx) -> None:
            await tx.execute(
                "INSERT INTO rt_edges (topic, follower) VALUES (?, ?) ON CONFLICT DO NOTHING",
                topic.name,
                str(follower),
            )

        await self._tx(do)

    async def unfollow(self, follower: Actor, topic: Topic) -> None:
        async def do(tx: Tx) -> None:
            await tx.execute(
                "DELETE FROM rt_edges WHERE topic = ? AND follower = ?",
                topic.name,
                str(follower),
            )

        await self._tx(do)

    async def followers_of(self, topic: Topic) -> list[Actor]:
        async def do(tx: Tx) -> list[Actor]:
            rows = await tx.fetchall(
                "SELECT follower FROM rt_edges WHERE topic = ? ORDER BY follower",
                topic.name,
            )
            return [Actor.from_str(r["follower"]) for r in rows]

        return await self._tx(do)

    # ------------------------------------------------------------------ operations

    async def stats(self) -> StoreStats:
        now_ts = _ts(self._clock())

        async def do(tx: Tx) -> StoreStats:
            counts = {
                r["status"]: int(r["c"])
                for r in await tx.fetchall(
                    "SELECT status, COUNT(*) AS c FROM rt_runs GROUP BY status"
                )
            }
            oldest = await tx.fetchone(
                "SELECT MIN(started_at) AS s FROM rt_runs WHERE status = 'running'"
            )
            dead = await tx.fetchone("SELECT COUNT(*) AS c FROM rt_dead_letters")
            return StoreStats(
                pending=counts.get("pending", 0),
                running=counts.get("running", 0),
                suspended=counts.get("suspended", 0),
                dead_letters=int(dead["c"]),  # type: ignore[index]
                oldest_lease_age_s=(now_ts - oldest["s"])
                if oldest and oldest["s"] is not None
                else 0.0,
            )

        return await self._tx(do)

    async def tree_spend(self, run_id: RunId) -> Spend:
        async def do(tx: Tx) -> Spend:
            row = await tx.fetchone(
                "SELECT s.tokens, s.cost_micros, s.turns FROM rt_spend s JOIN rt_runs r ON r.tree_id = s.tree_id WHERE r.run_id = ?",
                str(run_id),
            )
            if row is None:
                return Spend()
            return Spend(
                tokens=int(row["tokens"]),
                cost_usd=int(row["cost_micros"]) / 1_000_000,
                turns=int(row["turns"]),
            )

        return await self._tx(do)

    async def erase(self, *, tenant: str, thread_id: str | None = None) -> int:
        async def do(tx: Tx) -> int:
            where, args = "tenant = ?", [tenant]
            if thread_id is not None:
                where += " AND thread_id = ?"
                args.append(thread_id)
            rows = await tx.fetchall(
                f"SELECT run_id, agent FROM rt_runs WHERE {where}", *args
            )
            agents = {r["agent"] for r in rows}
            for r in rows:
                run_id = r["run_id"]
                for table in ("rt_events", "rt_run_wake", "rt_signals"):
                    await tx.execute(f"DELETE FROM {table} WHERE run_id = ?", run_id)
                await tx.execute("DELETE FROM rt_spawns WHERE child_run_id = ?", run_id)
            await tx.execute(f"DELETE FROM rt_runs WHERE {where}", *args)
            await tx.execute(
                "DELETE FROM rt_spend WHERE tree_id NOT IN (SELECT tree_id FROM rt_runs)"
            )
            for agent in agents:
                if (
                    await tx.fetchone(
                        "SELECT 1 AS x FROM rt_runs WHERE agent = ? LIMIT 1", agent
                    )
                    is None
                ):
                    for table in ("rt_inbox", "rt_inbox_processed", "rt_dead_letters"):
                        await tx.execute(f"DELETE FROM {table} WHERE agent = ?", agent)
            return len(rows)

        return await self._tx(do)

    async def prune(self, *, before: datetime) -> int:
        cutoff = _ts(before)

        async def do(tx: Tx) -> int:
            await tx.execute("DELETE FROM rt_inbox_processed WHERE at < ?", cutoff)
            old = await tx.fetchall(
                "SELECT run_id FROM rt_runs WHERE terminated_at IS NOT NULL AND terminated_at < ?",
                cutoff,
            )
            for r in old:
                for table in ("rt_events", "rt_run_wake", "rt_signals"):
                    await tx.execute(
                        f"DELETE FROM {table} WHERE run_id = ?", r["run_id"]
                    )
                await tx.execute("DELETE FROM rt_runs WHERE run_id = ?", r["run_id"])
            await tx.execute(
                "DELETE FROM rt_spend WHERE tree_id NOT IN (SELECT tree_id FROM rt_runs)"
            )
            return len(old)

        return await self._tx(do)


__all__ = ["DurableRuntimeStore"]
