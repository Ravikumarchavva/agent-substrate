"""Runtime — durable runtime over injectable kernel-Protocol backends.

The Runtime is backend-agnostic: every backend is injected and defaults to its
in-process implementation.  It never imports a concrete durable backend, so the
``agents`` layer stays strictly above ``capabilities``.

Usage (in-memory, Stage 0 — default)::

    async with Runtime() as rt:
        await rt.register(my_agent)
        run_id = await rt.submit(my_agent.id, boot_msg)

Usage (Postgres + Redis, Stage 1) — use the capabilities-layer factory, which
constructs the durable backends and injects them::

    from ravi.capabilities.runtime import build_postgres_runtime

    async with build_postgres_runtime(
        postgres_url="postgresql://...",
        redis_url="redis://localhost:6379/0",
    ) as rt:
        ...

All backends implement the same kernel Protocols, so agent code never
needs to know which backend is active.
"""

from __future__ import annotations

import uuid

from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import Message
from ravi.kernel.runtime.agent import Agent
from ravi.kernel.runtime.ids import RunId, RunStatus, new_run_id

from ravi.agents.runtime.backends._event_log import InMemoryEventLog
from ravi.agents.runtime.backends._fanout import PushAllFanout
from ravi.agents.runtime.backends._follow_graph import InMemoryFollowGraph
from ravi.agents.runtime.backends._inbox import InMemoryInbox
from ravi.agents.runtime.backends._journal import InMemoryJournal
from ravi.agents.runtime.backends._scheduler import InMemoryScheduler
from ravi.agents.runtime.backends._signal_bus import InMemorySignalBus
from ravi.agents.runtime.backends._supervisor import InMemorySupervisor
from ravi.agents.runtime.worker import Worker


class Runtime:
    """Durable runtime over injectable kernel-Protocol backends.

    Each backend defaults to its in-process implementation.  Pass durable
    backends (built by ``capabilities.runtime.build_postgres_runtime``) to run
    against Postgres/Redis.  The Runtime never imports a concrete durable
    backend itself, keeping ``agents`` strictly above ``capabilities``.
    """

    def __init__(
        self,
        *,
        event_log: object | None = None,
        inbox: object | None = None,
        journal: object | None = None,
        scheduler: object | None = None,
        signal_bus: object | None = None,
        follow_graph: object | None = None,
        fanout: object | None = None,
    ) -> None:
        self._event_log: object = event_log or InMemoryEventLog()
        self._journal: object = journal or InMemoryJournal()
        self._scheduler: object = scheduler or InMemoryScheduler()
        self._inbox: object = inbox or InMemoryInbox()
        # The inbox→runtime wakeup hook is a runtime concern; wire it on whatever
        # inbox was injected (or the default).
        self._inbox.set_deliver_hook(self._on_inbox_deliver)  # type: ignore[attr-defined]
        self._follow_graph = follow_graph or InMemoryFollowGraph()
        self._fanout = fanout or PushAllFanout()
        self._signal_bus = signal_bus or InMemorySignalBus()
        self._registry: dict[AgentId, Agent] = {}
        self._worker: Worker | None = None

    def _on_inbox_deliver(self, agent_id: AgentId) -> None:
        """Sync hook called by Inbox.deliver(); schedules an async dispatch task."""
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.call_soon(
            lambda: asyncio.create_task(self._handle_inbox_delivery(agent_id))
        )

    async def _handle_inbox_delivery(self, agent_id: AgentId) -> None:
        """Async: decide whether to wake a suspended run or spawn a fresh one.

        - Suspended run → wake it.
        - PENDING / RUNNING run → no-op (active run will drain inbox).
        - No active run → spawn a fresh run.

        Uses ``scheduler.find_run_for_agent()`` so this works for both the
        in-memory and Postgres schedulers without accessing private attributes.
        """
        result = await self._scheduler.find_run_for_agent(agent_id)
        if result is None:
            await self._spawn_run_for_inbox(agent_id)
            return
        run_id, status = result
        if status == RunStatus.SUSPENDED:
            await self._scheduler.wake_suspended(run_id)

    async def _spawn_run_for_inbox(self, agent_id: AgentId) -> None:
        """Create a fresh run so a queued inbox message gets processed."""
        run_id = new_run_id()
        self._scheduler.register_run(run_id, agent_id)
        await self._scheduler.enqueue(run_id, priority=5, tenant="default")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def register(self, agent: Agent) -> None:
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
        supervisor = InMemorySupervisor(
            event_log=self._event_log,  # type: ignore[arg-type]
            inbox=self._inbox,  # type: ignore[arg-type]
            journal=self._journal,  # type: ignore[arg-type]
            scheduler=self._scheduler,  # type: ignore[arg-type]
        )
        self._worker = Worker(
            worker_id=f"worker-{uuid.uuid4().hex[:8]}",
            event_log=self._event_log,  # type: ignore[arg-type]
            journal=self._journal,  # type: ignore[arg-type]
            inbox=self._inbox,  # type: ignore[arg-type]
            follow_graph=self._follow_graph,
            fanout=self._fanout,
            scheduler=self._scheduler,  # type: ignore[arg-type]
            supervisor=supervisor,
            signal_bus=self._signal_bus,
            registry=self._registry,
        )
        await self._worker.start()

    async def stop(self) -> None:
        if self._worker is not None:
            await self._worker.stop()

    async def __aenter__(self) -> Runtime:
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
