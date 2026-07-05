"""InMemorySupervisor — Stage 0 in-process implementation of Supervisor.

``spawn`` is lifecycle: creates a child run, records the spawn in
``_spawn_effects`` (so replay returns the same child_run_id), delivers the
boot message, and enqueues the child.  It does NOT wait.  Mirrors
``PostgresSupervisor``'s dedicated ``substrate_spawn_effects`` table — spawn
dedup is a Supervisor-local concern, not the generic effect Journal (which no
longer exists; ``ctx.llm()``/``ctx.tool()`` dedup through the EventLog itself
via ``EffectCache.fold()``).

``cancel`` cascades through the child's subtree by recursively cancelling
every child of the cancelled run, then marks the run CANCELLED.

``children_of`` is used by a restarting parent to reattach to in-flight
children after a crash (Stage 0: never needed, but the API is correct).
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import TYPE_CHECKING, AsyncIterator

from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import Message
from substrate.kernel.runtime.ids import RunId, RunStatus, new_run_id
from substrate.kernel.runtime.supervisor import RunHandle, RunResult
from substrate.kernel.agent.supervision import Supervision

if TYPE_CHECKING:
    from substrate.agents.runtime.backends._event_log import InMemoryEventLog
    from substrate.agents.runtime.backends._inbox import InMemoryInbox
    from substrate.agents.runtime.backends._scheduler import InMemoryScheduler
    from substrate.agents.runtime.backends._signal_bus import InMemorySignalBus


class InMemorySupervisor:
    def __init__(
        self,
        event_log: InMemoryEventLog,
        inbox: InMemoryInbox,
        scheduler: InMemoryScheduler,
        signal_bus: InMemorySignalBus,
    ) -> None:
        self._event_log = event_log
        self._inbox = inbox
        self._scheduler = scheduler
        self._signal_bus = signal_bus
        self._spawn_effects: dict[str, RunId] = {}
        self._children: dict[RunId, list[RunHandle]] = defaultdict(list)
        self._parent_of: dict[RunId, RunId] = {}
        self._results: dict[RunId, RunResult] = {}
        self._events: dict[RunId, asyncio.Event] = {}
        self._supervision_of: dict[RunId, Supervision] = {}

    async def supervision_of(self, run_id: RunId) -> Supervision | None:
        return self._supervision_of.get(run_id)

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
        from substrate.kernel.runtime.effects import Effect
        from substrate.kernel.runtime.log_entry import RunLogEntry

        # effect_id derives ONLY from the caller's own replay-stable `path` —
        # never from anything computed fresh here (the parent's current
        # last_seq) or from boot.id (routinely a fresh uuid4() per replay
        # attempt). Both would drift attempt-to-attempt precisely because
        # spawning is what advances them, silently defeating the "replay
        # returns the same child_run_id" guarantee — see the kernel
        # Supervisor.spawn() docstring.
        effect_id = Effect.make_id(
            parent, path, "spawn", {"child_agent": str(child_agent)}
        )
        cached = self._spawn_effects.get(effect_id)
        if cached is not None:
            child_run_id: RunId = cached
        else:
            child_run_id = new_run_id()
            self._spawn_effects[effect_id] = child_run_id
            # Deliver boot message to child inbox, stamped with the caller's
            # replay-stable correlation_id so ctx.ask(handle, ...) can wait
            # for the reply without a second delivery. notify=False: we
            # enqueue the child run explicitly below, so the deliver-hook
            # must not also spawn a duplicate run (same race as Runtime.submit).
            boot_with_reply = boot.model_copy(
                update={"reply_to": parent, "correlation_id": correlation_id}
            )
            await self._inbox.deliver(child_agent, boot_with_reply, notify=False)
            # Register and enqueue the child run
            self._scheduler.register_run(child_run_id, child_agent)
            await self._scheduler.enqueue(child_run_id, priority=5, tenant="default")
            # Log spawn in parent's EventLog — ONLY on a genuine new spawn.
            # Logging this unconditionally (including on a cache hit) would
            # append a duplicate "child.spawned" entry on every replay.
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

        # Idempotent regardless of cache hit/miss — a replay of this same
        # spawn() call reconstructs the identical Supervision object anyway.
        self._supervision_of[child_run_id] = supervision

        handle = RunHandle(
            run_id=child_run_id,
            agent_id=child_agent,
            parent_run=parent,
            boot_correlation_id=correlation_id,
        )
        if handle not in self._children[parent]:
            self._children[parent].append(handle)
        self._parent_of[child_run_id] = parent
        return handle

    async def cancel(self, handle: RunHandle, *, reason: str = "cancelled") -> None:
        from substrate.kernel.runtime.log_entry import RunLogEntry

        # Recursively cancel children first
        for child in list(self._children.get(handle.run_id, [])):
            await self.cancel(child, reason=reason)

        self._scheduler._status[handle.run_id] = RunStatus.CANCELLED
        seq = await self._event_log.last_seq(handle.run_id)
        await self._event_log.append(
            handle.run_id,
            RunLogEntry(
                run_id=handle.run_id,
                seq=seq + 1,
                kind="run.cancelled",
                payload={"reason": reason},
            ),
            expected_seq=seq,
        )
        await self.finish_run(handle.run_id, RunStatus.CANCELLED)

    def children_of(self, parent: RunId) -> AsyncIterator[RunHandle]:
        return self._children_iter(parent)

    async def _children_iter(self, parent: RunId) -> AsyncIterator[RunHandle]:  # type: ignore[return]
        for handle in list(self._children.get(parent, [])):
            yield handle

    async def join(self, handle: RunHandle) -> RunResult:
        """Protocol conformance only — ``RunContext.join()`` never calls this.

        The actual suspend-based join lives in ``agents/runtime/context.py``
        (consumes a ``child:{run_id}`` signal via the SignalBus, raising
        ``SuspendInterrupt`` on a miss so the Task genuinely ends rather than
        blocking). This asyncio.Event-based wait is dead weight on that path
        but kept for Protocol conformance / any future direct caller.
        """
        run_id = handle.run_id
        if run_id in self._results:
            return self._results[run_id]

        event = self._events.get(run_id)
        if event is None:
            event = asyncio.Event()
            self._events[run_id] = event

        await event.wait()
        return self._results[run_id]

    async def finish_run(
        self, run_id: RunId, status: RunStatus, *, error: str | None = None
    ) -> None:
        res = RunResult(run_id=run_id, status=status, error=error)
        self._results[run_id] = res

        event = self._events.pop(run_id, None)
        if event is not None:
            event.set()
        else:
            event = asyncio.Event()
            event.set()
            self._events[run_id] = event

        parent = self._parent_of.get(run_id)
        if parent is not None:
            await self._signal_bus.signal(
                parent, f"child:{run_id}", {"status": status.value, "error": error}
            )

        # run_id is terminal — it will never consume() again. Drop its
        # buffered-but-unclaimed signals now rather than leaking them for
        # the life of the process (see InMemorySignalBus.gc docstring).
        gc = getattr(self._signal_bus, "gc", None)
        if gc is not None:
            gc(run_id)
