"""LocalSupervisor — SQLite-durable SupervisorProtocol (no-infra tier).

Mirrors InMemorySupervisor's semantics: ``spawn`` dedups by a replay-stable
``effect_id`` so a replayed spawn returns the same ``child_run_id`` rather
than spawning twice; ``cancel`` cascades through the child subtree; results
are recorded durably so ``join`` survives a restart between spawn and
completion (unlike the in-memory version's ``asyncio.Event``, which cannot).
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

from substrate.kernel.runtime.log_entry import RunLogKind
from substrate.kernel.core.identity import Actor
from substrate.kernel.messaging.message import Message
from substrate.kernel.runtime.ids import RunId, RunStatus, new_run_id
from substrate.kernel.runtime.supervisor import RunHandle, RunResult
from substrate.kernel.agent.supervision import Priority, Supervision

from ._local_db import LocalRuntimeDB
from ._local_event_log import LocalEventLog
from ._local_inbox import LocalInbox
from ._local_scheduler import LocalScheduler
from ._local_signal_bus import LocalSignalBus

_POLL_INTERVAL_S = 0.2


class LocalSupervisor:
    """SQLite-backed SupervisorProtocol."""

    def __init__(
        self,
        db: LocalRuntimeDB,
        event_log: LocalEventLog,
        inbox: LocalInbox,
        scheduler: LocalScheduler,
        signal_bus: LocalSignalBus,
    ) -> None:
        self._db = db
        self._event_log = event_log
        self._inbox = inbox
        self._scheduler = scheduler
        self._signal_bus = signal_bus

    async def supervision_of(self, run_id: RunId) -> Supervision | None:
        def _do(conn: sqlite3.Connection) -> str | None:
            row = conn.execute(
                "SELECT supervision_json FROM supervisor_supervision WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return row["supervision_json"] if row else None

        raw = await self._db.run(_do)
        return Supervision.from_dict(json.loads(raw)) if raw else None

    async def spawn(
        self,
        child_agent: Actor,
        *,
        parent: RunId,
        supervision: Supervision,
        boot: Message,
        path: str,
        correlation_id: str,
    ) -> RunHandle:
        from substrate.kernel.runtime.effects import Effect
        from substrate.kernel.runtime.log_entry import RunLogEntry

        effect_id = Effect.make_id(
            parent, path, "spawn", {"child_agent": str(child_agent)}
        )

        def _read_cached(conn: sqlite3.Connection) -> str | None:
            row = conn.execute(
                "SELECT child_run_id FROM supervisor_spawn_effects WHERE effect_id = ?",
                (effect_id,),
            ).fetchone()
            return row["child_run_id"] if row else None

        cached = await self._db.run(_read_cached)

        if cached is not None:
            child_run_id: RunId = RunId(cached)
        else:
            active = await self._count_active(supervision.run_id)
            max_agents = supervision.spawn_budget.max_agents
            if 1 + active >= max_agents:
                from substrate.kernel.exceptions import BudgetExhaustedError

                raise BudgetExhaustedError(
                    f"Run headcount cap reached ({1 + active}/{max_agents} "
                    f"agents) for root {supervision.run_id!r}. Cannot spawn "
                    f"'{child_agent}'. This is the durable enforcement point "
                    "— it applies regardless of caller, unlike the "
                    "in-process SpawnTracker fast-path OrchestratorAgent "
                    "uses, which only covers spawns it mediates."
                )

            child_run_id = new_run_id()
            boot_with_reply = boot.model_copy(
                update={"reply_to": parent, "correlation_id": correlation_id}
            )

            def _record_spawn(conn: sqlite3.Connection) -> None:
                conn.execute(
                    "INSERT OR IGNORE INTO supervisor_spawn_effects (effect_id, child_run_id) "
                    "VALUES (?, ?)",
                    (effect_id, child_run_id),
                )

            await self._db.run(_record_spawn)
            await self._inbox.deliver(child_agent, boot_with_reply, notify=False)
            self._scheduler.register_run(child_run_id, child_agent)
            await self._scheduler.enqueue(
                child_run_id, priority=Priority.NORMAL, tenant="default"
            )
            seq = await self._event_log.last_seq(parent)
            await self._event_log.append(
                parent,
                RunLogEntry(
                    run_id=parent,
                    seq=seq + 1,
                    kind=RunLogKind.CHILD_SPAWNED,
                    payload={
                        "child_run_id": child_run_id,
                        "child_agent": str(child_agent),
                    },
                ),
                expected_seq=seq,
            )

        handle = RunHandle(
            run_id=child_run_id,
            agent_id=child_agent,
            parent_run=parent,
            boot_correlation_id=correlation_id,
        )

        def _record_child(conn: sqlite3.Connection) -> None:
            conn.execute(
                "INSERT INTO supervisor_supervision (run_id, supervision_json) VALUES (?, ?) "
                "ON CONFLICT (run_id) DO UPDATE SET supervision_json = excluded.supervision_json",
                (child_run_id, json.dumps(supervision.to_dict())),
            )
            conn.execute(
                "INSERT OR IGNORE INTO supervisor_children "
                "(parent_run_id, child_run_id, handle_json) VALUES (?, ?, ?)",
                (parent, child_run_id, handle.model_dump_json()),
            )

        await self._db.run(_record_child)
        return handle

    async def _count_active(self, root_run: RunId) -> int:
        """Count non-terminal descendants of *root_run*, recursively —
        mirrors InMemorySupervisor's own recursive walk over ``_children``."""

        def _do(conn: sqlite3.Connection) -> list[tuple[str, str]]:
            rows = conn.execute(
                "SELECT parent_run_id, child_run_id FROM supervisor_children"
            ).fetchall()
            return [(r["parent_run_id"], r["child_run_id"]) for r in rows]

        all_edges = await self._db.run(_do)
        children_of: dict[str, list[str]] = {}
        for p, c in all_edges:
            children_of.setdefault(p, []).append(c)

        def _has_result(run_id: str) -> bool:
            def _q(conn: sqlite3.Connection) -> bool:
                return (
                    conn.execute(
                        "SELECT 1 FROM supervisor_results WHERE run_id = ?", (run_id,)
                    ).fetchone()
                    is not None
                )

            return _q(self._db._conn)

        total = 0

        def _walk(run_id: str) -> None:
            nonlocal total
            for child in children_of.get(run_id, []):
                if not _has_result(child):
                    total += 1
                _walk(child)

        _walk(root_run)
        return total

    async def cancel(self, handle: RunHandle, *, reason: str = "cancelled") -> None:
        from substrate.kernel.runtime.log_entry import RunLogEntry

        def _children(conn: sqlite3.Connection) -> list[str]:
            rows = conn.execute(
                "SELECT handle_json FROM supervisor_children WHERE parent_run_id = ?",
                (handle.run_id,),
            ).fetchall()
            return [r["handle_json"] for r in rows]

        for handle_json in await self._db.run(_children):
            child_handle = RunHandle.model_validate_json(handle_json)
            await self.cancel(child_handle, reason=reason)

        await self._scheduler.force_cancel(handle.run_id)
        seq = await self._event_log.last_seq(handle.run_id)
        await self._event_log.append(
            handle.run_id,
            RunLogEntry(
                run_id=handle.run_id,
                seq=seq + 1,
                kind=RunLogKind.RUN_CANCELLED,
                payload={"reason": reason},
            ),
            expected_seq=seq,
        )
        await self.finish_run(handle.run_id, RunStatus.CANCELLED)

    def children_of(self, parent: RunId):
        return self._children_iter(parent)

    async def _children_iter(self, parent: RunId):  # type: ignore[return]
        def _do(conn: sqlite3.Connection) -> list[str]:
            rows = conn.execute(
                "SELECT handle_json FROM supervisor_children WHERE parent_run_id = ?",
                (parent,),
            ).fetchall()
            return [r["handle_json"] for r in rows]

        for handle_json in await self._db.run(_do):
            yield RunHandle.model_validate_json(handle_json)

    async def join(self, handle: RunHandle) -> RunResult:
        """Protocol conformance only — ``RunContext.join()`` never calls
        this (see InMemorySupervisor.join's docstring for the real path).
        Polls for a durable result rather than an in-process Event, since
        the writer of that result may be a different process than the one
        calling join()."""
        run_id = handle.run_id
        while True:
            result = await self._get_result(run_id)
            if result is not None:
                return result
            await asyncio.sleep(_POLL_INTERVAL_S)

    async def _get_result(self, run_id: RunId) -> RunResult | None:
        def _do(conn: sqlite3.Connection) -> str | None:
            row = conn.execute(
                "SELECT result_json FROM supervisor_results WHERE run_id = ?", (run_id,)
            ).fetchone()
            return row["result_json"] if row else None

        raw = await self._db.run(_do)
        return RunResult.model_validate_json(raw) if raw else None

    async def finish_run(
        self, run_id: RunId, status: RunStatus, *, error: str | None = None
    ) -> None:
        res = RunResult(run_id=run_id, status=status, error=error)

        def _do(conn: sqlite3.Connection) -> str | None:
            conn.execute(
                "INSERT INTO supervisor_results (run_id, result_json) VALUES (?, ?) "
                "ON CONFLICT (run_id) DO UPDATE SET result_json = excluded.result_json",
                (run_id, res.model_dump_json()),
            )
            row = conn.execute(
                "SELECT parent_run_id FROM supervisor_children WHERE child_run_id = ?",
                (run_id,),
            ).fetchone()
            return row["parent_run_id"] if row else None

        parent = await self._db.run(_do)
        if parent is not None:
            await self._signal_bus.signal(
                RunId(parent), f"child:{run_id}", {"status": status.value, "error": error}
            )

        self._signal_bus.gc(run_id)


__all__ = ["LocalSupervisor"]
