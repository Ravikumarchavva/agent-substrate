"""Runtime — durable runtime over injectable kernel-Protocol backends.

The Runtime is backend-agnostic: every backend is injected and defaults to its
in-process implementation.  It never imports a concrete durable backend, so the
``agents`` layer stays strictly above ``infrastructure``.

Usage (in-memory, Stage 0 — default)::

    async with Runtime() as rt:
        await rt.register(my_agent)
        run_id = await rt.submit(my_agent.id, boot_msg)

Usage (Postgres + Redis, Stage 1) — use the infrastructure-layer factory, which
constructs the durable backends and injects them::

    from substrate.infrastructure.runtime import build_postgres_runtime

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
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from substrate.agents.core.orchestrator import SubAgentConfig

from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import Message
from substrate.agents.runtime.context import Agent
from substrate.kernel.runtime.fanout import FanoutStrategy
from substrate.kernel.runtime.follow_graph import FollowGraph
from substrate.kernel.runtime.ids import RunId, RunStatus, new_run_id
from substrate.kernel.runtime.inbox import InboxProtocol
from substrate.kernel.runtime.log_entry import EventLogProtocol
from substrate.kernel.runtime.scheduler import RunRetryPolicy, SchedulerProtocol
from substrate.kernel.runtime.supervisor import SupervisorProtocol
from substrate.kernel.runtime.wakeup import SignalBusProtocol

from substrate.agents.runtime.backends._event_log import InMemoryEventLog
from substrate.agents.runtime.backends._fanout import PushAllFanout
from substrate.agents.runtime.backends._follow_graph import InMemoryFollowGraph
from substrate.agents.runtime.backends._inbox import InMemoryInbox
from substrate.agents.runtime.backends._scheduler import InMemoryScheduler
from substrate.agents.runtime.backends._signal_bus import InMemorySignalBus
from substrate.agents.runtime.backends._supervisor import InMemorySupervisor
from substrate.agents.runtime.worker import Worker


@dataclass
class RunOutcome:
    """Result of ``Runtime.run()`` — the ergonomic, one-shot run API.

    ``output`` is the agent's final assistant text (``None`` if the run
    produced no text or did not complete). ``error`` carries the failure
    message when ``status`` is ``FAILED``.
    """

    run_id: str
    status: RunStatus
    output: str | None = None
    error: str | None = None
    metadata: dict = field(default_factory=dict)

    def __str__(self) -> str:
        return self.output or ""


class Runtime:
    """Durable runtime over injectable kernel-Protocol backends.

    Each backend defaults to its in-process implementation.  Pass durable
    backends (built by ``infrastructure.runtime.build_postgres_runtime``) to run
    against Postgres/Redis.  The Runtime never imports a concrete durable
    backend itself, keeping ``agents`` strictly above ``infrastructure``.
    """

    def __init__(
        self,
        *,
        event_log: EventLogProtocol | None = None,
        inbox: InboxProtocol | None = None,
        scheduler: SchedulerProtocol | None = None,
        signal_bus: SignalBusProtocol | None = None,
        supervisor: SupervisorProtocol | None = None,
        follow_graph: FollowGraph | None = None,
        fanout: FanoutStrategy | None = None,
    ) -> None:
        self._event_log: EventLogProtocol = event_log or InMemoryEventLog()
        self._scheduler: SchedulerProtocol = scheduler or InMemoryScheduler()
        self._inbox: InboxProtocol = inbox or InMemoryInbox()
        # The inbox→runtime wakeup hook is a runtime concern; wire it on whatever
        # inbox was injected (or the default).
        self._inbox.set_deliver_hook(self._on_inbox_deliver)  # type: ignore[attr-defined]
        self._follow_graph: FollowGraph = follow_graph or InMemoryFollowGraph()
        self._fanout: FanoutStrategy = fanout or PushAllFanout()
        # The default in-memory SignalBusProtocol wakes suspended runs via the
        # scheduler it's paired with — see backends/_signal_bus.py.
        self._signal_bus: SignalBusProtocol = signal_bus or InMemorySignalBus(
            self._scheduler  # type: ignore[arg-type]
        )
        self._supervisor: SupervisorProtocol | None = supervisor
        self._registry: dict[AgentId, Agent] = {}
        self._worker: Worker | None = None

    def _on_inbox_deliver(self, agent_id: AgentId) -> None:
        """Sync hook called by InboxProtocol.deliver(); schedules an async dispatch task."""
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
        """Register an agent so the runtime can dispatch runs to it.

        If ``agent`` is an OrchestratorAgent whose ``_sub_agents`` list is
        populated, every sub-agent is also registered automatically.  This
        prevents the Worker from silently holding the lease (for up to its
        timeout) when the orchestrator spawns a child that isn't in the
        registry.
        """
        self._registry[agent.id] = agent
        sub_agents: list[SubAgentConfig] = getattr(agent, "_sub_agents", [])
        for cfg in sub_agents:
            self._registry[cfg.agent.id] = cfg.agent

    async def submit(
        self,
        agent_id: AgentId,
        msg: Message,
        *,
        priority: int = 5,
        tenant: str = "default",
        max_retries: int = 3,
        retry_policy: RunRetryPolicy | None = None,
        thread_id: str | None = None,
    ) -> RunId:
        """Deliver ``msg`` to ``agent_id`` and enqueue a run.

        Returns the new run_id.  The run starts when the Worker next polls.
        Pass ``max_retries=0`` for interactive runs where the journal-replay
        retry loop would cause repeated failures on the same journaled error.

        ``retry_policy``, if given, is used verbatim (``max_retries`` is
        ignored) — for callers that need to tune ``backoff_s``/
        ``max_backoff_s`` rather than just the retry count.

        ``thread_id`` (optional) enforces durable, cross-replica single-flight
        for the conversation thread this run belongs to — a second
        ``submit(..., thread_id=X)`` while thread X already has an active run
        raises ``kernel.core.errors.ThreadBusyError`` instead of enqueuing.
        On that (rare) rejection, ``msg`` may already be sitting in
        ``agent_id``'s inbox — delivery happens first, before the
        single-flight check, because the run must find its own message
        already there the instant it's leasable (delivery has to happen
        before enqueue, not after — a worker can lease and start draining
        the inbox as soon as the queue row exists, and a message that lands
        a moment later would be invisible to that first drain). A caller
        that wires ``thread_id`` is expected to have already done its own
        cheap pre-check (see ``routes/chat.py``) so this collision is rare;
        the orphaned inbox entry is picked up by the next legitimate run for
        this agent_id, same as an unsolicited delivery would be.
        """
        run_id = new_run_id()
        self._scheduler.register_run(run_id, agent_id)
        # Deliver with notify=False: this submit() explicitly enqueues its own
        # run below, so the inbox deliver-hook (_handle_inbox_delivery) must NOT
        # fire. If it did, it would race against our enqueue() — finding no
        # active run yet via find_run_for_agent() and spawning a DUPLICATE run
        # for this same message. The hook is only for unsolicited deliveries
        # (publish/fanout/inter-agent ask) that have no accompanying submit.
        await self._inbox.deliver(agent_id, msg, notify=False)
        await self._scheduler.enqueue(
            run_id,
            priority=priority,
            tenant=tenant,
            retry_policy=retry_policy or RunRetryPolicy(max_retries=max_retries),
            thread_id=thread_id,
        )
        return run_id

    async def run(
        self,
        agent: Agent,
        prompt: str,
        *,
        tenant: str = "default",
    ) -> RunOutcome:
        """Run ``agent`` on a single ``prompt`` and wait for the final answer.

        The ergonomic one-shot entry point: registers the agent, delivers the
        prompt as a chat message, submits a run, tails its EventLogProtocol until the
        run reaches a terminal state, and returns a :class:`RunOutcome` whose
        ``output`` is the agent's final assistant text.

        For streaming, multi-message, or durable-suspend workflows use
        ``register`` + ``submit`` and tail ``event_log`` yourself — this helper
        is for the common "ask once, get one answer" case.
        """
        from substrate.kernel.core.content import ChatMessage, Role, TextBlock
        from substrate.kernel.messaging.message import ChatPayload

        await self.register(agent)
        msg = Message(
            target=agent.id,
            sender=AgentId(type="user", key="run"),
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text=prompt)])
            ),
        )
        # max_retries=0: a one-shot interactive run must not replay-retry a
        # journaled error (see AgentStreamSession).
        run_id = await self.submit(agent.id, msg, tenant=tenant, max_retries=0)

        text_acc = ""
        async for entry in self._event_log.tail(run_id):
            kind = entry.kind
            payload = entry.payload or {}
            if kind == "tool.result":
                # New turn boundary — the final answer is the text produced
                # after the last tool result.
                text_acc = ""
            elif kind == "text.delta":
                text_acc += payload.get("text", "")
            elif kind == "run.completed":
                return RunOutcome(
                    run_id=run_id, status=RunStatus.COMPLETED, output=text_acc or None
                )
            elif kind == "run.failed":
                return RunOutcome(
                    run_id=run_id,
                    status=RunStatus.FAILED,
                    error=payload.get("error", "agent run failed"),
                )
            elif kind == "run.cancelled":
                return RunOutcome(run_id=run_id, status=RunStatus.CANCELLED)
        return RunOutcome(
            run_id=run_id, status=RunStatus.COMPLETED, output=text_acc or None
        )

    async def ask(
        self,
        agent: Agent,
        prompt: str,
        *,
        timeout: float = 120.0,
        tenant: str = "default",
    ) -> RunOutcome:
        """Run an ask/reply-style ``agent`` (e.g. a ``fabric.flows`` pipeline) and
        wait for its ``ctx.reply()`` result.

        Coordination primitives (``SequentialFlow``, ``ParallelFlow``,
        ``ConditionalFlow``, or any hand-written agent that calls
        ``ctx.reply(msg, ...)`` instead of streaming text via ``ctx.llm()``)
        don't produce ``text.delta`` log entries, so :meth:`run` can't capture
        their output. This method mirrors how one agent invokes another via
        ``ctx.ask()``: the entry message carries a synthetic ``reply_to``, and
        the result is read directly off the ``SignalBusProtocol`` — the same
        mechanism ``ctx.ask()`` itself consumes.

        Register any of ``agent``'s dependencies (e.g. a flow's ``steps``)
        with ``Runtime.register()`` before calling this.
        """
        import asyncio

        from substrate.kernel.core.content import ChatMessage, Role, TextBlock
        from substrate.kernel.messaging.message import ChatPayload

        await self.register(agent)
        sentinel = new_run_id()
        msg = Message(
            target=agent.id,
            sender=AgentId(type="user", key="ask"),
            payload=ChatPayload(
                message=ChatMessage(role=Role.USER, content=[TextBlock(text=prompt)])
            ),
            reply_to=sentinel,
        )
        run_id = await self.submit(agent.id, msg, tenant=tenant, max_retries=0)

        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            payload = await self._signal_bus.consume(
                sentinel, f"reply:{msg.correlation_id}", f"runtime-ask:{sentinel}"
            )
            if payload is not None:
                return RunOutcome(
                    run_id=run_id,
                    status=RunStatus.COMPLETED,
                    output=payload.get("text") or None,
                    error=payload.get("error") or None,
                )
            await asyncio.sleep(0.02)
        return RunOutcome(
            run_id=run_id, status=RunStatus.FAILED, error="timed out waiting for reply"
        )

    async def follow(
        self, follower: AgentId, topic_type: str, topic_source: str
    ) -> None:
        """Subscribe ``follower`` to a topic."""
        from substrate.kernel.core.identity import TopicId

        await self._follow_graph.follow(
            follower, TopicId(type=topic_type, source=topic_source)
        )

    async def publish(self, topic_type: str, topic_source: str, msg: Message) -> None:
        """Publish ``msg`` to all followers of a topic."""
        from substrate.kernel.core.identity import TopicId

        topic = TopicId(type=topic_type, source=topic_source)
        await self._fanout.publish(
            topic, msg, graph=self._follow_graph, inbox=self._inbox
        )

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._supervisor is None:
            # InMemorySupervisor reaches into InMemoryScheduler's private state
            # (see backends/_supervisor.py), so it can only stand in as the
            # default when every other backend is also the in-memory one — a
            # caller mixing durable backends must supply an explicit supervisor.
            assert isinstance(self._event_log, InMemoryEventLog)
            assert isinstance(self._inbox, InMemoryInbox)
            assert isinstance(self._scheduler, InMemoryScheduler)
            assert isinstance(self._signal_bus, InMemorySignalBus)
            self._supervisor = InMemorySupervisor(
                event_log=self._event_log,
                inbox=self._inbox,
                scheduler=self._scheduler,
                signal_bus=self._signal_bus,
            )
        supervisor = self._supervisor
        self._worker = Worker(
            worker_id=f"worker-{uuid.uuid4().hex}",
            event_log=self._event_log,
            inbox=self._inbox,
            follow_graph=self._follow_graph,
            fanout=self._fanout,
            scheduler=self._scheduler,
            supervisor=supervisor,
            signal_bus=self._signal_bus,
            registry=self._registry,
        )
        await self._worker.start()

    async def stop(self) -> None:
        if self._worker is not None:
            await self._worker.stop()

    async def cancel(self, run_id: str) -> None:
        """Cancel an in-flight run."""
        if self._worker is not None:
            await self._worker.cancel(run_id)

    async def __aenter__(self) -> Runtime:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Internal accessors (for tests)
    # ------------------------------------------------------------------

    @property
    def event_log(self) -> EventLogProtocol:
        return self._event_log

    @property
    def inbox(self) -> InboxProtocol:
        return self._inbox

    @property
    def scheduler(self) -> SchedulerProtocol:
        # Exposed for find_run_for_thread() — serving code resolves a
        # conversation thread's active run_id durably (works across
        # replicas) instead of keeping its own thread_id → run_id registry.
        return self._scheduler

    @property
    def signal_bus(self) -> SignalBusProtocol:
        # Whatever backend was injected (InMemorySignalBus by default, or a
        # durable backend via build_postgres_runtime) — serving/console code
        # only ever calls .signal() on this, which every backend implements
        # identically per the kernel Protocol.
        return self._signal_bus

    @property
    def supervisor(self) -> SupervisorProtocol:
        """The active SupervisorProtocol — ``None`` until ``start()``/``__aenter__``.

        Exposed for cascading ``cancel(handle)`` (recursive subtree
        cancellation — distinct from ``Runtime.cancel(run_id)`` above, which
        only cancels a single in-process Task) and other SupervisorProtocol-level
        operations (``children_of`` for crash reconciliation).
        """
        assert self._supervisor is not None, "Runtime not started"
        return self._supervisor
