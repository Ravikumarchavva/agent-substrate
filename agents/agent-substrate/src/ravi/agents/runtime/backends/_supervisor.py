"""InMemorySupervisor — Stage 0 in-process implementation of Supervisor.

``spawn`` is lifecycle: creates a child run, journals the spawn (so replay
returns the same child_run_id), delivers the boot message, and enqueues
the child.  It does NOT wait.

``cancel`` cascades through the child's subtree by recursively cancelling
every child of the cancelled run, then marks the run CANCELLED.

``children_of`` is used by a restarting parent to reattach to in-flight
children after a crash (Stage 0: never needed, but the API is correct).
"""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, AsyncIterator

from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import Message
from ravi.kernel.runtime.ids import RunId, RunStatus, new_run_id
from ravi.kernel.runtime.supervisor import RunHandle
from ravi.kernel.agent.supervision import Supervision

if TYPE_CHECKING:
    from ravi.agents.runtime.backends._event_log import InMemoryEventLog
    from ravi.agents.runtime.backends._inbox import InMemoryInbox
    from ravi.agents.runtime.backends._journal import InMemoryJournal
    from ravi.agents.runtime.backends._scheduler import InMemoryScheduler


class InMemorySupervisor:
    def __init__(
        self,
        event_log: InMemoryEventLog,
        inbox: InMemoryInbox,
        journal: InMemoryJournal,
        scheduler: InMemoryScheduler,
    ) -> None:
        self._event_log = event_log
        self._inbox = inbox
        self._journal = journal
        self._scheduler = scheduler
        self._children: dict[RunId, list[RunHandle]] = defaultdict(list)

    async def spawn(
        self,
        child_agent: AgentId,
        *,
        parent: RunId,
        supervision: Supervision,
        boot: Message,
    ) -> RunHandle:
        from ravi.kernel.runtime.effects import Effect, EffectResult
        from ravi.kernel.runtime.log_entry import RunLogEntry

        effect_id = Effect.make_id(
            parent,
            await self._event_log.last_seq(parent) + 1,
            "spawn",
            {"child_agent": str(child_agent), "boot_id": boot.id},
        )
        cached = await self._journal.lookup(effect_id)
        if cached:
            child_run_id: RunId = cached.value["child_run_id"]
        else:
            child_run_id = new_run_id()
            await self._journal.record(
                EffectResult(
                    effect_id=effect_id,
                    status="ok",
                    value={"child_run_id": child_run_id},
                )
            )
            # Deliver boot message to child inbox
            boot_with_reply = boot.model_copy(update={"reply_to": parent})
            await self._inbox.deliver(child_agent, boot_with_reply)
            # Register and enqueue the child run
            self._scheduler.register_run(child_run_id, child_agent)
            await self._scheduler.enqueue(child_run_id, priority=5, tenant="default")

        handle = RunHandle(
            run_id=child_run_id,
            agent_id=child_agent,
            parent_run=parent,
        )
        if handle not in self._children[parent]:
            self._children[parent].append(handle)

        # Log spawn in parent's EventLog
        seq = await self._event_log.last_seq(parent)
        await self._event_log.append(
            parent,
            RunLogEntry(
                run_id=parent,
                seq=seq + 1,
                kind="child.spawned",
                payload={"child_run_id": child_run_id, "child_agent": str(child_agent)},
            ),
            expected_seq=seq,
        )
        return handle

    async def cancel(self, handle: RunHandle, *, reason: str = "cancelled") -> None:
        from ravi.kernel.runtime.log_entry import RunLogEntry

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

    def children_of(self, parent: RunId) -> AsyncIterator[RunHandle]:
        return self._children_iter(parent)

    async def _children_iter(self, parent: RunId) -> AsyncIterator[RunHandle]:  # type: ignore[return]
        for handle in list(self._children.get(parent, [])):
            yield handle
