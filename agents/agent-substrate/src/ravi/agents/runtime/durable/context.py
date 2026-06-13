"""DurableContext — the L1 journaled execution context for durable agents.

This is what the agent author receives as ``ctx`` in ``agent.run(ctx, inbox)``.

Every capability method is journaled via the Journal + Effect system:
- On the **live path**: the real operation runs; the result is recorded.
- On the **replay path**: Journal.lookup() returns the cached result; the
  real operation is skipped entirely.

This gives the at-most-once guarantee: even if the worker crashes mid-run
and a new worker replays from the EventLog, effects that already completed
are never re-executed.

Stage 0 note
------------
In Stage 0 (in-process asyncio) the coroutine is never serialised — it
stays alive as an asyncio Task.  ``ask`` and ``sleep_until_signal`` suspend
via ``asyncio.Event`` (the lightweight in-process equivalent of the durable
``Wakeup`` mechanism).  Stage 1 replaces these with real suspend/resume from
the EventLog; the agent author's code is unchanged.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ravi.kernel.core.content import JsonObject
from ravi.kernel.core.errors import CancellationError
from ravi.kernel.core.identity import AgentId, TopicId
from ravi.kernel.messaging.message import Message, DataPayload
from ravi.kernel.runtime.communication import AskOutcome, RunStatusSummary
from ravi.kernel.runtime.effects import Effect, EffectResult
from ravi.kernel.runtime.ids import RunId, RunStatus, new_run_id
from ravi.kernel.runtime.log_entry import RunLogEntry
from ravi.kernel.runtime.supervisor import RunHandle, RunResult
from ravi.kernel.agent.supervision import Supervision

if TYPE_CHECKING:
    from ravi.agents.runtime.durable._event_log import InMemoryEventLog
    from ravi.agents.runtime.durable._fanout import PushAllFanout
    from ravi.agents.runtime.durable._follow_graph import InMemoryFollowGraph
    from ravi.agents.runtime.durable._inbox import InMemoryInbox
    from ravi.agents.runtime.durable._journal import InMemoryJournal
    from ravi.agents.runtime.durable._scheduler import InMemoryScheduler
    from ravi.agents.runtime.durable._signal_bus import InMemorySignalBus
    from ravi.agents.runtime.durable._supervisor import InMemorySupervisor


class DurableContext:
    """Journaled execution context — satisfies DurableContextProtocol.

    Created fresh by the Worker for each agent.run() invocation.
    ``_step_seq`` increments with every journaled operation, forming the
    effect_id namespace for this run.
    """

    def __init__(
        self,
        *,
        run_id: RunId,
        tenant_id: str | None,
        event_log: InMemoryEventLog,
        journal: InMemoryJournal,
        inbox: InMemoryInbox,
        follow_graph: InMemoryFollowGraph,
        fanout: PushAllFanout,
        scheduler: InMemoryScheduler,
        supervisor: InMemorySupervisor,
        signal_bus: InMemorySignalBus,
        cancellation: asyncio.Event | None = None,
    ) -> None:
        self.run_id = run_id
        self.tenant_id = tenant_id
        self._event_log = event_log
        self._journal = journal
        self._inbox = inbox
        self._follow_graph = follow_graph
        self._fanout = fanout
        self._scheduler = scheduler
        self._supervisor = supervisor
        self._signal_bus = signal_bus
        self._cancelled = cancellation or asyncio.Event()
        self._step_seq = 0

    # ------------------------------------------------------------------
    # DurableContextProtocol surface
    # ------------------------------------------------------------------

    def check(self) -> None:
        """Raise CancellationError if this run has been cancelled."""
        if self._cancelled.is_set():
            raise CancellationError("run cancelled")
        status = self._scheduler.get_status(self.run_id)
        if status == RunStatus.CANCELLED:
            self._cancelled.set()
            raise CancellationError("run cancelled")

    # ------------------------------------------------------------------
    # Journaled generic effect helper
    # ------------------------------------------------------------------

    async def _journaled(
        self,
        kind: str,
        args: JsonObject,
        fn: Any,
    ) -> Any:
        """Run fn() with at-most-once semantics via the Journal."""
        effect_id = Effect.make_id(self.run_id, self._step_seq, kind, args)
        self._step_seq += 1
        cached = await self._journal.lookup(effect_id)
        if cached:
            if cached.status == "error":
                raise RuntimeError(cached.value.get("error", "journaled error"))
            return cached.value
        try:
            result = await fn()
            await self._journal.record(
                EffectResult(effect_id=effect_id, status="ok", value=result or {})
            )
            return result
        except Exception as exc:
            await self._journal.record(
                EffectResult(
                    effect_id=effect_id, status="error", value={"error": str(exc)}
                )
            )
            raise

    async def _log(self, kind: str, payload: JsonObject = {}) -> None:
        seq = await self._event_log.last_seq(self.run_id)
        await self._event_log.append(
            self.run_id,
            RunLogEntry(run_id=self.run_id, seq=seq + 1, kind=kind, payload=payload),
            expected_seq=seq,
        )

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send(self, target: AgentId, msg: Message) -> None:
        """Fire-and-forget delivery.  Does not suspend the caller."""
        await self._inbox.deliver(target, msg)
        # Ensure the target's run is enqueued if it exists
        for run_id, agent_id in list(self._scheduler._agents.items()):
            if agent_id == target:
                status = self._scheduler.get_status(run_id)
                if status in (RunStatus.SUSPENDED, None):
                    await self._scheduler.wake_suspended(run_id)
                break

    async def emit(self, topic: TopicId, msg: Message) -> None:
        """Publish to all followers of ``topic`` (fire-and-forget)."""
        await self._fanout.publish(
            topic, msg, graph=self._follow_graph, inbox=self._inbox
        )

    async def ask(
        self,
        target: AgentId | RunHandle,
        msg: Message,
        *,
        timeout: float,
        idempotency_key: str | None = None,
    ) -> AskOutcome:
        """Send ``msg`` and suspend until a reply, timeout, or target failure.

        Sets ``msg.reply_to = self.run_id`` and ``msg.correlation_id``
        automatically.  The target calls ``ctx.reply(msg, result)`` to
        complete the ask.
        """
        self.check()

        correlation_id = idempotency_key or msg.correlation_id
        enriched = msg.model_copy(
            update={"reply_to": self.run_id, "correlation_id": correlation_id}
        )

        target_agent: AgentId = (
            target.agent_id if isinstance(target, RunHandle) else target
        )
        target_run: RunId | None = (
            target.run_id if isinstance(target, RunHandle) else None
        )

        # Deliver to target inbox
        await self._inbox.deliver(target_agent, enriched)
        # Wake target if suspended
        if target_run:
            await self._scheduler.wake_suspended(target_run)
        else:
            for run_id, agent_id in list(self._scheduler._agents.items()):
                if agent_id == target_agent:
                    await self._scheduler.wake_suspended(run_id)
                    target_run = run_id
                    break

        await self._log(
            "ask.sent", {"target": str(target_agent), "correlation_id": correlation_id}
        )

        signal_name = f"reply:{correlation_id}"
        try:
            payload = await self._signal_bus.wait_for_signal(
                self.run_id, signal_name, timeout=timeout
            )
            result = RunResult(
                run_id=target_run or "",
                status=RunStatus.COMPLETED,
                output=DataPayload(data=payload),
            )
            await self._log("ask.replied", {"correlation_id": correlation_id})
            return AskOutcome(kind="replied", result=result, last_seq=0)

        except asyncio.TimeoutError:
            last_seq = -1
            if target_run:
                last_seq = await self._event_log.last_seq(target_run)
                target_status = self._scheduler.get_status(target_run)
            else:
                target_status = None

            if target_status in (RunStatus.FAILED,):
                kind = "target_failed"
            elif target_status == RunStatus.CANCELLED:
                kind = "target_cancelled"
            else:
                kind = "timed_out"

            handle = (
                target
                if isinstance(target, RunHandle)
                else RunHandle(
                    run_id=target_run or new_run_id(),
                    agent_id=target_agent,
                    parent_run=self.run_id,
                )
            )
            await self._log(
                "ask.timeout", {"correlation_id": correlation_id, "kind": kind}
            )
            return AskOutcome(kind=kind, handle=handle, last_seq=last_seq)

    async def reply(self, to: Message, result: JsonObject) -> None:
        """Send a reply to an ``ask``.  Signals the asker's run."""
        if to.reply_to:
            await self._signal_bus.signal(
                to.reply_to,
                f"reply:{to.correlation_id}",
                result,
            )

    async def status(self, handle: RunHandle) -> RunStatusSummary:
        """Opt-in batched peek at a run's progress.  Not a stream."""
        run_status = self._scheduler.get_status(handle.run_id) or RunStatus.PENDING
        last_seq = await self._event_log.last_seq(handle.run_id)
        last_milestone: str | None = None
        if last_seq >= 0:
            async for entry in self._event_log.read(handle.run_id, from_seq=last_seq):
                last_milestone = entry.kind
        return RunStatusSummary(
            run_id=handle.run_id,
            status=run_status,
            last_seq=last_seq,
            last_milestone=last_milestone,
        )

    # ------------------------------------------------------------------
    # Lifecycle — spawn / cancel
    # ------------------------------------------------------------------

    async def spawn(
        self,
        child_agent: AgentId,
        *,
        boot: Message,
        supervision: Supervision | None = None,
    ) -> RunHandle:
        """Spawn a child run.  Returns a handle; does NOT wait for completion."""
        self.check()
        sup = supervision or Supervision.root(child_agent)
        return await self._supervisor.spawn(
            child_agent, parent=self.run_id, supervision=sup, boot=boot
        )

    async def cancel(self, handle: RunHandle, *, reason: str = "cancelled") -> None:
        """Cancel a child run and its entire subtree."""
        await self._supervisor.cancel(handle, reason=reason)

    # ------------------------------------------------------------------
    # Suspension primitives
    # ------------------------------------------------------------------

    async def sleep_until_signal(self, name: str) -> JsonObject:
        """Suspend until a named signal arrives on this run."""
        self.check()
        await self._log("run.suspended", {"waiting_for": name})
        result = await self._signal_bus.wait_for_signal(self.run_id, name)
        await self._log("run.resumed", {"signal": name})
        return result

    async def sleep_until(self, dt: datetime) -> None:
        """Suspend until a wall-clock time."""
        self.check()
        await self._log("run.suspended", {"until": dt.isoformat()})
        delay = max(0.0, (dt - datetime.now(tz=timezone.utc)).total_seconds())
        await asyncio.sleep(delay)
        await self._log("run.resumed", {"via": "timer"})

    # ------------------------------------------------------------------
    # Social graph
    # ------------------------------------------------------------------

    async def follow(self, topic: TopicId) -> None:
        """Subscribe this agent to a topic."""
        agent_id = AgentId(type="run", key=self.run_id)
        await self._follow_graph.follow(agent_id, topic)

    async def unfollow(self, topic: TopicId) -> None:
        from ravi.kernel.messaging.message import Subscription

        agent_id = AgentId(type="run", key=self.run_id)
        sub = Subscription(topic=topic, agent_id=agent_id)
        await self._follow_graph.unfollow(sub)

    # ------------------------------------------------------------------
    # Deterministic helpers (for replay safety)
    # ------------------------------------------------------------------

    def now(self) -> datetime:
        """Journaled wall-clock — use this instead of datetime.now()."""
        return datetime.now(tz=timezone.utc)
