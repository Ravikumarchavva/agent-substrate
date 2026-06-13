"""DurableRuntime — Stage 0 single-process wiring.

Drop-in replacement for LocalRuntime when you want durable semantics
(EventLog, Journal, Inbox, FollowGraph) within a single process.

Usage::

    async with DurableRuntime() as rt:
        await rt.register(my_agent)
        run_id = await rt.submit(my_agent.id, boot_msg)
        # agent is now running in the background

All components are in-memory; nothing survives process restart (Stage 1
swaps in Postgres).  The kernel contracts are identical at every stage.
"""

from __future__ import annotations

import uuid

from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import Message
from ravi.kernel.runtime.agent import DurableAgent
from ravi.kernel.runtime.ids import RunId, RunStatus, new_run_id

from ravi.agents.runtime.durable._event_log import InMemoryEventLog
from ravi.agents.runtime.durable._fanout import PushAllFanout
from ravi.agents.runtime.durable._follow_graph import InMemoryFollowGraph
from ravi.agents.runtime.durable._inbox import InMemoryInbox
from ravi.agents.runtime.durable._journal import InMemoryJournal
from ravi.agents.runtime.durable._scheduler import InMemoryScheduler
from ravi.agents.runtime.durable._signal_bus import InMemorySignalBus
from ravi.agents.runtime.durable._supervisor import InMemorySupervisor
from ravi.agents.runtime.durable.worker import Worker


class DurableRuntime:
    """Stage 0 in-process durable runtime.

    Wires all in-memory components together and starts a single Worker
    that polls the Scheduler and dispatches agent runs as asyncio Tasks.
    """

    def __init__(self) -> None:
        self._event_log = InMemoryEventLog()
        self._journal = InMemoryJournal()
        self._scheduler = InMemoryScheduler()
        self._inbox = InMemoryInbox(
            on_deliver=self._on_inbox_deliver,
        )
        self._follow_graph = InMemoryFollowGraph()
        self._fanout = PushAllFanout()
        self._signal_bus = InMemorySignalBus()
        self._supervisor = InMemorySupervisor(
            event_log=self._event_log,
            inbox=self._inbox,
            journal=self._journal,
            scheduler=self._scheduler,
        )
        self._registry: dict[AgentId, DurableAgent] = {}
        self._worker = Worker(
            worker_id=f"worker-{uuid.uuid4().hex[:8]}",
            event_log=self._event_log,
            journal=self._journal,
            inbox=self._inbox,
            follow_graph=self._follow_graph,
            fanout=self._fanout,
            scheduler=self._scheduler,
            supervisor=self._supervisor,
            signal_bus=self._signal_bus,
            registry=self._registry,
        )

    def _on_inbox_deliver(self, agent_id: AgentId) -> None:
        """Hook: called by InMemoryInbox after a new message is stored.

        - Suspended run → wake it and return.
        - None / PENDING / RUNNING run → no-op (run is active or about to be).
        - All runs COMPLETED or FAILED → spawn a fresh run so the message is
          picked up (this is the auto-respawn path for reply/fan-out use cases).

        NOTE: this method is called synchronously from inside ``await deliver()``,
        so there is always a running event loop at call time.  We use
        ``get_running_loop()`` instead of ``get_event_loop()`` to guarantee we
        get the loop that owns this runtime (not a stale loop from a prior test).
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no running loop → cannot schedule anything

        for run_id, aid in list(self._scheduler._agents.items()):
            if aid == agent_id:
                status = self._scheduler.get_status(run_id)
                if status == RunStatus.SUSPENDED:
                    loop.call_soon(
                        lambda rid=run_id: asyncio.create_task(
                            self._scheduler.wake_suspended(rid)
                        )
                    )
                    return
                if status not in (RunStatus.COMPLETED, RunStatus.FAILED):
                    # None (registered, not yet enqueued), PENDING, RUNNING —
                    # all mean there is an active run that will drain this msg.
                    return

        # Every known run is completed/failed, or no run exists at all.
        # Create a fresh run so the inbox message gets processed.
        loop.call_soon(lambda: asyncio.create_task(self._spawn_run_for_inbox(agent_id)))

    async def _spawn_run_for_inbox(self, agent_id: AgentId) -> None:
        """Create a fresh run so a queued inbox message gets processed."""
        run_id = new_run_id()
        self._scheduler.register_run(run_id, agent_id)
        await self._scheduler.enqueue(run_id, priority=5, tenant="default")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def register(self, agent: DurableAgent) -> None:
        """Register an agent so the runtime can dispatch runs to it."""
        self._registry[agent.id] = agent

    async def submit(
        self,
        agent_id: AgentId,
        msg: Message,
        *,
        priority: int = 5,
        tenant: str = "default",
    ) -> RunId:
        """Deliver ``msg`` to ``agent_id`` and enqueue a run.

        Returns the new run_id.  The run starts when the Worker next polls.
        """
        run_id = new_run_id()
        self._scheduler.register_run(run_id, agent_id)
        await self._inbox.deliver(agent_id, msg)
        await self._scheduler.enqueue(run_id, priority=priority, tenant=tenant)
        return run_id

    async def follow(
        self, follower: AgentId, topic_type: str, topic_source: str
    ) -> None:
        """Subscribe ``follower`` to a topic."""
        from ravi.kernel.core.identity import TopicId

        await self._follow_graph.follow(
            follower, TopicId(type=topic_type, source=topic_source)
        )

    async def publish(self, topic_type: str, topic_source: str, msg: Message) -> None:
        """Publish ``msg`` to all followers of a topic."""
        from ravi.kernel.core.identity import TopicId

        topic = TopicId(type=topic_type, source=topic_source)
        await self._fanout.publish(
            topic, msg, graph=self._follow_graph, inbox=self._inbox
        )

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def start(self) -> None:
        await self._worker.start()

    async def stop(self) -> None:
        await self._worker.stop()

    async def __aenter__(self) -> DurableRuntime:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Internal accessors (for tests)
    # ------------------------------------------------------------------

    @property
    def event_log(self) -> InMemoryEventLog:
        return self._event_log

    @property
    def inbox(self) -> InMemoryInbox:
        return self._inbox

    @property
    def signal_bus(self) -> InMemorySignalBus:
        return self._signal_bus
