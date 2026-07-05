"""RunContext supervision mixin — spawn/cancel/join, timed/signal suspension.

Split out of ``context/__init__.py`` (see that module's docstring for the full
suspend/resume/replay contract this all serves). Depends on
``_JournalMixin``'s path/log helpers — see the ``TYPE_CHECKING`` stubs below.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from substrate.kernel.core.content import JsonObject
from substrate.kernel.core.errors import SuspendInterrupt
from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import Message
from substrate.kernel.runtime.effects import Effect
from substrate.kernel.runtime.ids import RunStatus
from substrate.kernel.runtime.supervisor import RunHandle, RunResult
from substrate.kernel.runtime.wakeup import Wakeup
from substrate.kernel.agent.supervision import Supervision

if TYPE_CHECKING:
    from substrate.kernel.agent.runtime_context import RunMeta
    from substrate.kernel.runtime.log_entry import EventLog
    from substrate.kernel.runtime.wakeup import SignalBus
    from substrate.kernel.runtime.supervisor import Supervisor


class _SupervisionMixin:
    """Child-run lifecycle (spawn/cancel/join) and timed/signal suspension."""

    if TYPE_CHECKING:
        run_id: str
        _meta: RunMeta
        _supervisor: Supervisor
        _event_log: EventLog
        _seq_cursor: int
        _signal_bus: SignalBus

        def check(self) -> None: ...
        def _alloc_path(self) -> str: ...
        async def _log(self, kind: str, payload: JsonObject = ...) -> None: ...

    async def spawn(
        self,
        child_agent: AgentId,
        *,
        boot: Message,
        supervision: Supervision | None = None,
    ) -> RunHandle:
        """Spawn a child run.  Returns a handle; does NOT wait for completion."""
        self.check()
        if supervision is not None:
            sup = supervision
        elif self._meta.supervision is not None:
            # Inherit the caller's own execution_budget/spawn_budget (this
            # run was itself ctx.spawn()'d, and its Supervision was
            # persisted by Supervisor.spawn() and rehydrated by the Worker
            # at lease time — see RunMeta.supervision / Worker._run_agent).
            # spawn_child() defaults the child's execution_budget to the
            # parent's; previously this branch never existed and every
            # child got Supervision.root() unconditionally — an unlimited,
            # unrelated-to-parent budget regardless of what constraints the
            # spawning run itself was under.
            sup = self._meta.supervision.spawn_child(child_agent)
        else:
            sup = Supervision.root(child_agent)
        # The spawn effect's identity, and the boot message's correlation_id,
        # must come from OUR OWN replay-stable path allocation — never from
        # anything the Supervisor computes fresh (e.g. the parent log's
        # current last_seq) or from boot.id/boot.correlation_id (agent
        # authors routinely construct a fresh Message, with fresh
        # auto-generated ids, on every call to their own run()). Any of
        # those would drift across replay attempts: the first defeats
        # "replaying returns the same child_run_id", the second means a
        # later ctx.ask(handle, ...) can never find the reply it's waiting
        # for (see that method's docstring for the full trace).
        path = self._alloc_path()
        correlation_id = f"{self.run_id}.{path}"
        handle = await self._supervisor.spawn(
            child_agent,
            parent=self.run_id,
            supervision=sup,
            boot=boot,
            path=path,
            correlation_id=correlation_id,
        )
        # Supervisor.spawn() appends a "child.spawned" entry directly to this
        # run's own EventLog (bypassing ctx._log — it has no ctx reference,
        # only the shared event_log), so the local seq cursor must be
        # resynced here or the next ctx._log() call would see a stale
        # expected_seq and raise ConcurrentAppendError.
        self._seq_cursor = await self._event_log.last_seq(self.run_id)
        return handle

    async def cancel(self, handle: RunHandle, *, reason: str = "cancelled") -> None:
        """Cancel a child run and its entire subtree."""
        await self._supervisor.cancel(handle, reason=reason)

    async def join(self, handle: RunHandle) -> RunResult:
        """Suspend the parent until the child run reaches a terminal state.

        Non-blocking claim, not a wait (same model as ``ask`` and
        ``sleep_until_signal``): the Supervisor's ``finish_run`` fires a
        ``child:{run_id}`` signal when the child reaches a terminal state,
        so this consumes that signal — a miss raises ``SuspendInterrupt``
        rather than parking on ``Supervisor.join()`` (which blocks a live
        coroutine and would not survive a process restart).
        """
        self.check()
        child_run = handle.run_id
        signal_name = f"child:{child_run}"
        path = self._alloc_path()
        effect_id = Effect.make_id(
            self.run_id, path, "join.wait", {"child_run": child_run}
        )
        payload = await self._signal_bus.consume(self.run_id, signal_name, effect_id)
        if payload is not None:
            status = RunStatus(payload["status"])
            await self._log(
                "join.completed", {"child_run": child_run, "status": status.value}
            )
            return RunResult(
                run_id=child_run, status=status, error=payload.get("error")
            )
        await self._log("run.suspended", {"waiting_for": signal_name})
        raise SuspendInterrupt(
            self.run_id,
            Wakeup(kind="signal", signals=[signal_name]),
            reason=f"join:{child_run}",
        )

    # ------------------------------------------------------------------
    # Suspension primitives
    # ------------------------------------------------------------------

    async def sleep_until_signal(self, name: str) -> JsonObject:
        """Suspend until a named signal arrives on this run.

        Non-blocking claim, not a wait: a miss raises ``SuspendInterrupt``
        (see module docstring) rather than parking a coroutine. The
        ``effect_id`` is deterministic (from this call's hierarchical path),
        so a replay that reaches this same wait re-claims the SAME payload
        it already consumed — or, if nothing has arrived yet, misses again
        and re-suspends, identically to the first attempt.
        """
        self.check()
        path = self._alloc_path()
        effect_id = Effect.make_id(self.run_id, path, "signal.wait", {"name": name})
        payload = await self._signal_bus.consume(self.run_id, name, effect_id)
        if payload is not None:
            await self._log("run.resumed", {"signal": name})
            return payload
        await self._log("run.suspended", {"waiting_for": name})
        raise SuspendInterrupt(
            self.run_id,
            Wakeup(kind="signal", signals=[name]),
            reason=f"sleep_until_signal:{name}",
        )

    async def sleep_until(self, dt: datetime) -> None:
        """Suspend until a wall-clock time.

        Deliberately re-checks the REAL clock (not the journaled ``ctx.now()``
        helper) on every attempt, live or replayed — the whole point of this
        wait is to observe actual wall-clock progress across suspensions;
        journaling it would freeze the check at whatever time the first
        attempt happened and it would never appear to have arrived.
        """
        self.check()
        if datetime.now(tz=timezone.utc) >= dt:
            await self._log("run.resumed", {"via": "timer"})
            return
        await self._signal_bus.timer(self.run_id, dt)
        await self._log("run.suspended", {"until": dt.isoformat()})
        raise SuspendInterrupt(
            self.run_id,
            Wakeup(kind="timer", at=dt),
            reason=f"sleep_until:{dt.isoformat()}",
        )


__all__ = ["_SupervisionMixin"]
