"""Runtime — multi-stage durable runtime.

Usage (in-memory, Stage 0 — default)::

    async with Runtime() as rt:
        await rt.register(my_agent)
        run_id = await rt.submit(my_agent.id, boot_msg)

Usage (Postgres + Redis, Stage 1)::

    async with Runtime(
        backend="postgres",
        postgres_url="postgresql://...",
        redis_url="redis://localhost:6379/0",
    ) as rt:
        ...

All backends implement the same kernel Protocols, so agent code never
needs to know which stage is active.
"""

from __future__ import annotations

import uuid
from typing import Literal

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
    """Multi-stage durable runtime.

    ``backend="memory"`` (default) — all in-process, lost on restart.
    ``backend="postgres"`` — Postgres EventLog/Inbox/Scheduler + Redis Journal.
    Postgres tables are created on ``start()`` (``IF NOT EXISTS``).
    """

    def __init__(
        self,
        *,
        backend: Literal["memory", "postgres"] = "memory",
        postgres_url: str | None = None,
        redis_url: str | None = None,
        journal_ttl_seconds: int = 86400,
    ) -> None:
        self._backend = backend
        self._postgres_url = postgres_url
        self._redis_url = redis_url
        self._journal_ttl_seconds = journal_ttl_seconds
        self._pg_pool: object | None = None

        # All start as in-memory; start() swaps in durable backends when needed.
        self._event_log: object = InMemoryEventLog()
        self._journal: object = InMemoryJournal()
        self._scheduler: object = InMemoryScheduler()
        self._inbox: object = InMemoryInbox(on_deliver=self._on_inbox_deliver)
        self._follow_graph = InMemoryFollowGraph()
        self._fanout = PushAllFanout()
        self._signal_bus = InMemorySignalBus()
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
        if self._backend == "postgres":
            await self._start_postgres_backends()

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

    async def _start_postgres_backends(self) -> None:
        """Swap in Postgres + Redis backends. Called only when backend='postgres'."""
        import asyncpg  # type: ignore[import]

        from ravi.capabilities.runtime import (
            PostgresEventLog,
            PostgresInbox,
            PostgresScheduler,
        )

        url = self._postgres_url
        if url is None:
            from ravi.config import settings
            url = settings.DATABASE_URL.replace("+asyncpg", "")

        self._pg_pool = await asyncpg.create_pool(url)
        pool = self._pg_pool  # type: ignore[assignment]

        self._event_log = PostgresEventLog(pool)
        self._scheduler = PostgresScheduler(pool)
        self._inbox = PostgresInbox(
            pool,
            on_deliver=self._on_inbox_deliver,
        )

        await self._event_log.setup()   # type: ignore[attr-defined]
        await self._scheduler.setup()   # type: ignore[attr-defined]
        await self._inbox.setup()       # type: ignore[attr-defined]

        if self._redis_url is not None:
            from ravi.capabilities.runtime import RedisJournal
            import redis.asyncio as aioredis  # type: ignore[import]

            redis_client = aioredis.from_url(self._redis_url)
            self._journal = RedisJournal(
                redis_client,
                ttl_seconds=self._journal_ttl_seconds,
            )

    async def stop(self) -> None:
        if self._worker is not None:
            await self._worker.stop()
        if self._pg_pool is not None:
            await self._pg_pool.close()  # type: ignore[attr-defined]

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
