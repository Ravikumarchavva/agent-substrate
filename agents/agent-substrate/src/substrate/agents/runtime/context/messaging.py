"""RunContext messaging mixin — send/emit/ask/reply, status, follow graph.

Split out of ``context/__init__.py`` (see that module's docstring for the full
suspend/resume/replay contract this all serves). Depends on
``_JournalMixin``'s path/log/effect helpers — see the ``TYPE_CHECKING``
stubs below.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Literal

from substrate.kernel.core.content import JsonObject
from substrate.kernel.core.errors import SuspendInterrupt
from substrate.kernel.core.identity import AgentId, TopicId
from substrate.kernel.messaging.message import Message, DataPayload
from substrate.kernel.runtime.communication import AskOutcome, RunStatusSummary
from substrate.kernel.runtime.effects import Effect, EffectResult
from substrate.kernel.runtime.ids import RunId, RunStatus, new_run_id
from substrate.kernel.runtime.supervisor import RunHandle, RunResult
from substrate.kernel.runtime.wakeup import Wakeup

if TYPE_CHECKING:
    from substrate.kernel.runtime.log_entry import EventLogProtocol
    from substrate.kernel.runtime.inbox import InboxProtocol
    from substrate.kernel.runtime.scheduler import SchedulerProtocol
    from substrate.kernel.runtime.wakeup import SignalBusProtocol
    from substrate.kernel.runtime.fanout import FanoutStrategy
    from substrate.kernel.runtime.follow_graph import FollowGraph


class _MessagingMixin:
    """Point-to-point send/ask/reply, pub/sub emit, status peek, follow graph."""

    if TYPE_CHECKING:
        run_id: str
        _inbox: InboxProtocol
        _scheduler: SchedulerProtocol
        _fanout: FanoutStrategy
        _follow_graph: FollowGraph
        _signal_bus: SignalBusProtocol
        _event_log: EventLogProtocol

        def check(self) -> None: ...
        def _alloc_path(self) -> str: ...
        def _lookup_effect(self, effect_id: str) -> EffectResult | None: ...
        async def _log(self, kind: str, payload: JsonObject = ...) -> None: ...
        async def _record_effect(
            self, effect_id: str, status: Literal["ok", "error"], value: JsonObject
        ) -> None: ...
        async def _resolve_effect_value(self, result: EffectResult) -> JsonObject: ...

    async def send(self, target: AgentId, msg: Message) -> None:
        """Fire-and-forget delivery.  Does not suspend the caller."""
        await self._inbox.deliver(target, msg)
        await self._scheduler.wake_agent(target)

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
        """Send ``msg`` and suspend until a reply, deadline, or target failure.

        Two call shapes, handled differently:

        - ``target`` is a ``RunHandle`` from a PRIOR ``ctx.spawn()``: the
          child is already running, already booted with a message. This call
          only WAITS, using ``handle.boot_correlation_id`` — it does not
          send anything. (Re-sending ``msg`` here — even a copy — would
          collide with the InboxProtocol's idempotent-by-message-id dedup the moment
          the caller reuses the same ``Message`` object for both the
          ``spawn(boot=msg)`` and this call, a natural and common pattern:
          whichever delivery lands second is silently dropped, and if it's
          this one, the correlation_id this wait listens on is never the one
          the child actually replies with.)
        - ``target`` is a plain ``AgentId``: this call both sends and waits.
          The correlation_id is derived from this call's own replay-stable
          path, NOT taken from ``msg.correlation_id`` (unless
          ``idempotency_key`` is given explicitly) — a caller that builds a
          fresh ``Message`` with an auto-generated correlation_id on every
          call to its own ``run()`` would otherwise get a DIFFERENT id on
          every replay attempt, silently defeating both the at-most-once
          send guarantee and the ability to ever find a reply buffered under
          a previous attempt's id.

        The target calls ``ctx.reply(msg, result)`` to complete the ask.  If
        ``target`` is a ``RunHandle``, a supervisor that fires
        ``child:{run_id}`` signals on completion (see
        ``Supervisor``/``InMemorySupervisor``) makes a
        crashed/cancelled child resolve immediately instead of only after
        the full timeout — this wait watches both names at once.
        """
        self.check()

        target_agent: AgentId = (
            target.agent_id if isinstance(target, RunHandle) else target
        )
        target_run: RunId | None = (
            target.run_id if isinstance(target, RunHandle) else None
        )
        handle = (
            target
            if isinstance(target, RunHandle)
            else RunHandle(
                run_id=target_run or new_run_id(),
                agent_id=target_agent,
                parent_run=self.run_id,
            )
        )

        send_path = self._alloc_path()
        already_booted = isinstance(target, RunHandle) and target.boot_correlation_id
        if already_booted:
            correlation_id = target.boot_correlation_id  # type: ignore[assignment]
        else:
            correlation_id = idempotency_key or f"{self.run_id}.{send_path}"
            enriched = msg.model_copy(
                update={"reply_to": self.run_id, "correlation_id": correlation_id}
            )
            # Journaled send: delivering the ask must happen at most once,
            # even if this run crashes and replays right after sending.
            send_effect_id = Effect.make_id(
                self.run_id, send_path, "ask.send", {"correlation_id": correlation_id}
            )
            if self._lookup_effect(send_effect_id) is None:
                await self._inbox.deliver(target_agent, enriched)
                if target_run:
                    await self._scheduler.wake_suspended(target_run)
                else:
                    await self._scheduler.wake_agent(target_agent)
                await self._log(
                    "ask.sent",
                    {"target": str(target_agent), "correlation_id": correlation_id},
                )
                await self._record_effect(send_effect_id, "ok", {})

        # Deadline is journaled too — frozen at the first attempt, so replay
        # doesn't push it back out every time this wait is re-entered.
        deadline_path = self._alloc_path()
        deadline_effect_id = Effect.make_id(
            self.run_id,
            deadline_path,
            "ask.deadline",
            {"correlation_id": correlation_id},
        )
        deadline = await self._deadline_for(deadline_effect_id, timeout)

        reply_name = f"reply:{correlation_id}"
        child_name = f"child:{target_run}" if target_run else None
        wait_names = [reply_name] + ([child_name] if child_name else [])

        wait_path = self._alloc_path()
        wait_effect_id = Effect.make_id(
            self.run_id, wait_path, "ask.wait", {"correlation_id": correlation_id}
        )

        for name in wait_names:
            payload = await self._signal_bus.consume(
                self.run_id, name, f"{wait_effect_id}:{name}"
            )
            if payload is None:
                continue
            if name == reply_name:
                result = RunResult(
                    run_id=target_run or "",
                    status=RunStatus.COMPLETED,
                    output=DataPayload(data=payload),
                )
                await self._log("ask.replied", {"correlation_id": correlation_id})
                return AskOutcome(kind="replied", result=result, last_seq=0)
            # child_name fired: the supervisor reports how the child ended.
            kind = payload.get("kind", "target_failed")
            await self._log(
                "ask.timeout", {"correlation_id": correlation_id, "kind": kind}
            )
            return AskOutcome(kind=kind, handle=handle, last_seq=-1)

        if datetime.now(tz=timezone.utc) >= deadline:
            await self._log(
                "ask.timeout", {"correlation_id": correlation_id, "kind": "timed_out"}
            )
            return AskOutcome(kind="timed_out", handle=handle, last_seq=-1)

        # Not resolved yet and not past deadline: arrange the wake (needed
        # for Stage 0's real timer; a no-op-beyond-wake_at for Postgres,
        # whose release() below already sets it from the Wakeup) and suspend.
        await self._signal_bus.timer(self.run_id, deadline)
        await self._log(
            "run.suspended",
            {"waiting_for": wait_names, "deadline": deadline.isoformat()},
        )
        raise SuspendInterrupt(
            self.run_id,
            Wakeup(kind="signal", signals=wait_names, at=deadline),
            reason=f"ask:{correlation_id}",
        )

    async def _deadline_for(self, effect_id: str, timeout: float) -> datetime:
        """Journaled, frozen-at-first-attempt deadline (now + timeout)."""
        cached = self._lookup_effect(effect_id)
        if cached is not None:
            value = await self._resolve_effect_value(cached)
            return datetime.fromisoformat(value["deadline"])
        deadline = datetime.now(tz=timezone.utc) + timedelta(seconds=timeout)
        await self._record_effect(effect_id, "ok", {"deadline": deadline.isoformat()})
        return deadline

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
        run_status = (
            await self._scheduler.get_status(handle.run_id) or RunStatus.PENDING
        )
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
    # Social graph
    # ------------------------------------------------------------------

    async def follow(self, topic: TopicId) -> None:
        """Subscribe this agent to a topic."""
        agent_id = AgentId(type="run", key=self.run_id)
        await self._follow_graph.follow(agent_id, topic)

    async def unfollow(self, topic: TopicId) -> None:
        from substrate.kernel.messaging.message import Subscription

        agent_id = AgentId(type="run", key=self.run_id)
        sub = Subscription(topic=topic, agent_id=agent_id)
        await self._follow_graph.unfollow(sub)


__all__ = ["_MessagingMixin"]
