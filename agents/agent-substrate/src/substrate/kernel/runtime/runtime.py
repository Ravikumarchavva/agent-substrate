"""Runtime — the durable engine, over one ``RuntimeStore``.

::

    async with Runtime.local("./data/runtime.sqlite3") as rt:
        await rt.register(agent)
        outcome = await rt.run(agent, "What is the capital of France?")

A runtime owns a worker that leases runs from its store and executes them, and the
store's lifecycle: ``async with`` starts both and stops both. Everything it needs to
survive a restart is in the store, so a run submitted before a crash is picked up
after one.

The store is the only thing that varies between deployments — the SQLite file here,
a Postgres database for workers on several machines — and every engine behaviour
(retries, leases, replay, supervision) is the same across them, because it lives
here and not in the store.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from substrate.kernel.abstractions.agent.supervision import Priority
from substrate.kernel.abstractions.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.abstractions.core.identity import Actor, Topic
from substrate.kernel.abstractions.core.trace import TraceContext
from substrate.kernel.abstractions.exceptions import ThreadBusyError
from substrate.kernel.abstractions.ids import new_id
from substrate.kernel.abstractions.messaging.message import ChatPayload, Message
from substrate.kernel.abstractions.runtime.ids import RunId, RunStatus
from substrate.kernel.abstractions.runtime.log_entry import RunLogEntry, RunLogKind
from substrate.kernel.abstractions.runtime.scheduler import RunRetryPolicy
from substrate.kernel.abstractions.runtime.store import Delivery, RunRecord, RunSpec, RuntimeStore
from substrate.kernel.runtime.resolver import ActorFactory, ActorResolver
from substrate.kernel.runtime.tail import tail
from substrate.kernel.runtime.worker import Worker

if TYPE_CHECKING:
    from substrate.kernel.agents.orchestrator import SubAgentConfig

_TERMINAL_KINDS = frozenset(
    {RunLogKind.RUN_COMPLETED, RunLogKind.RUN_FAILED, RunLogKind.RUN_CANCELLED, RunLogKind.RUN_TRUNCATED}
)


@dataclass
class RunOutcome:
    """The result of ``Runtime.run`` — the one-shot "ask once, get one answer" API."""

    run_id: str
    status: RunStatus
    output: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.output or ""


class Runtime:
    """Registers agents, submits work, and runs it on a worker over a store."""

    def __init__(
        self,
        store: RuntimeStore,
        *,
        resolver: ActorResolver | None = None,
        max_concurrency: int = 10,
        lease_s: float = 30.0,
        poll_interval_s: float = 0.05,
    ) -> None:
        self._store = store
        self._resolver = resolver or ActorResolver()
        self._worker = Worker(
            worker_id=f"worker-{new_id()}",
            store=store,
            resolver=self._resolver,
            max_concurrency=max_concurrency,
            lease_s=lease_s,
            poll_interval_s=poll_interval_s,
        )
        self._started = False

    @classmethod
    def local(cls, path: str | Path = "./data/db/runtime.sqlite3", **options: Any) -> Runtime:
        """A runtime on a SQLite file: durable, no server, safe for workers on one host."""
        from substrate.kernel.runtime.sqlite_store import SqliteRuntimeStore

        return cls(SqliteRuntimeStore(path), **options)

    # ------------------------------------------------------------------ registration

    async def register(self, agent: Any, *, pinned: bool = True) -> None:
        """Make an agent runnable. ``pinned=False`` lets it be evicted when idle, for a
        call site that rebuilds one instance per entity on every call anyway.

        An orchestrator's configured sub-agents are registered with it: a child that is
        not registered would hold its lease until it expired.
        """
        self._resolver.register_instance(agent, pinned=pinned)
        sub_agents: list[SubAgentConfig] = getattr(agent, "_sub_agents", [])
        for cfg in sub_agents:
            self._resolver.register_instance(cfg.agent)

    def register_factory(self, actor_type: str, factory: ActorFactory) -> None:
        """Say how to build *any* actor of a type on demand — one entry per type
        instead of one pinned object per entity::

            runtime.register_factory("conversation", lambda actor: ReActAgent(..., session_id=actor.key))
        """
        self._resolver.register_factory(actor_type, factory)

    # ------------------------------------------------------------------ submitting work

    async def submit(
        self,
        agent_id: Actor,
        msg: Message,
        *,
        priority: Priority = Priority.NORMAL,
        tenant: str = "default",
        max_retries: int = 3,
        retry_policy: RunRetryPolicy | None = None,
        thread_id: str | None = None,
        recipe: dict[str, Any] | None = None,
    ) -> RunId:
        """Create a run for ``agent_id`` carrying ``msg`` and return its id.

        The message and the run are created in one step, so a worker that leases the run
        always finds its message. With ``thread_id`` the thread allows one active run at
        a time, across every worker sharing the store: a second ``submit`` for it raises
        ``ThreadBusyError`` and leaves nothing behind. ``recipe`` is opaque to the engine:
        whatever a host needs to rebuild this run's agent after a restart.
        """
        agent = await self._resolver.resolve(agent_id)
        run = await self._store.create_run(
            RunSpec(
                agent=agent_id,
                tenant=tenant,
                thread_id=thread_id,
                priority=priority,
                retry_policy=retry_policy or RunRetryPolicy(max_retries=max_retries),
                trace=TraceContext.new(),
                agent_version=str(getattr(agent, "version", "0")),
                recipe=recipe,
            ),
            deliveries=[Delivery(agent=agent_id, msg=msg, tenant=tenant)],
        )
        return run.run_id

    async def run(self, agent: Any, prompt: str, *, tenant: str = "default") -> RunOutcome:
        """Run an agent on one prompt and wait for its answer.

        For streaming, several messages, or runs that suspend, use ``register`` +
        ``submit`` and read ``tail`` yourself.
        """
        await self.register(agent)
        run_id = await self.submit(agent.id, _chat(Actor(type="user", key="run"), agent.id, prompt), tenant=tenant, max_retries=0)
        text = ""
        async for entry in self.tail(run_id):
            payload = entry.payload or {}
            if entry.kind == RunLogKind.TOOL_RESULT:
                text = ""  # a new turn: the answer is the last assistant message after the final tool result
            elif entry.kind == RunLogKind.ASSISTANT_MESSAGE:
                text = payload.get("text", "")
            elif entry.kind == RunLogKind.RUN_COMPLETED:
                return RunOutcome(run_id=run_id, status=RunStatus.COMPLETED, output=text or None)
            elif entry.kind == RunLogKind.RUN_FAILED:
                return RunOutcome(run_id=run_id, status=RunStatus.FAILED, error=payload.get("error", "agent run failed"))
            elif entry.kind == RunLogKind.RUN_CANCELLED:
                return RunOutcome(run_id=run_id, status=RunStatus.CANCELLED)
        return RunOutcome(run_id=run_id, status=RunStatus.COMPLETED, output=text or None)

    async def ask(self, agent: Any, prompt: str, *, timeout: float = 120.0, tenant: str = "default") -> RunOutcome:
        """Run an agent that answers with ``ctx.reply`` (a flow, say) and wait for the reply.

        Such agents do not stream text, so ``run`` cannot capture their output. This
        mirrors how one agent calls another: the message carries a ``reply_to`` and the
        reply is read off the signal buffer.
        """
        await self.register(agent)
        sentinel = RunId(new_id())
        msg = _chat(Actor(type="user", key="ask"), agent.id, prompt).model_copy(update={"reply_to": sentinel})
        run_id = await self.submit(agent.id, msg, tenant=tenant, max_retries=0)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            payload = await self._store.consume(sentinel, f"reply:{msg.correlation_id}", f"runtime-ask:{sentinel}")
            if payload is not None:
                return RunOutcome(run_id=run_id, status=RunStatus.COMPLETED, output=payload.get("text") or None, error=payload.get("error") or None)
            await asyncio.sleep(0.02)
        return RunOutcome(run_id=run_id, status=RunStatus.FAILED, error="timed out waiting for reply")

    async def follow(self, follower: Actor, topic_type: str, topic_source: str) -> None:
        await self._store.follow(follower, Topic(f"{topic_type}/{topic_source}"))

    async def publish(self, topic_type: str, topic_source: str, msg: Message) -> None:
        """Deliver ``msg`` to every follower of the topic."""
        for follower in await self._store.followers_of(Topic(f"{topic_type}/{topic_source}")):
            await self._store.deliver(Delivery(agent=follower, msg=msg))

    # ------------------------------------------------------------------ observing

    @property
    def store(self) -> RuntimeStore:
        return self._store

    def tail(self, run_id: RunId | str, *, from_seq: int = 0) -> AsyncIterator[RunLogEntry]:
        """A run's entries — live output included — as they happen. Never ends on its own:
        stop iterating when you see the terminal entry you care about."""
        return tail(self._store, run_id, from_seq=from_seq)

    async def read(self, run_id: RunId | str, *, from_seq: int = 0) -> list[RunLogEntry]:
        """A run's durable entries so far (no live output): what a replay would see."""
        return await self._store.read_events(RunId(run_id), from_seq=from_seq, durable_only=True)

    async def get_run(self, run_id: RunId | str) -> RunRecord | None:
        return await self._store.get_run(RunId(run_id))

    async def active_run_for_thread(self, thread_id: str) -> RunRecord | None:
        found = await self._store.find_runs(thread_id=thread_id)
        return found[0] if found else None

    async def runs_for_thread(self, thread_id: str) -> list[RunRecord]:
        """Every run a thread has had, oldest first: the basis for projecting its conversation."""
        return await self._store.find_runs(thread_id=thread_id, active_only=False)

    # ------------------------------------------------------------------ control

    async def cancel(self, run_id: RunId | str, *, reason: str = "cancelled") -> list[RunId]:
        """Cancel a run and everything it spawned. A run on this worker is interrupted
        now; one on another worker notices at its next heartbeat."""
        affected = await self._store.request_cancel(RunId(run_id), reason=reason)
        for affected_id in affected:
            self._worker.cancel_local(str(affected_id), reason)
        return affected

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._started:
            return
        await self._store.start()
        await self._worker.start()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        await self._worker.stop()
        await self._store.aclose()
        self._started = False

    async def __aenter__(self) -> Runtime:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.stop()


def _chat(sender: Actor, target: Actor, prompt: str) -> Message:
    return Message(
        target=target,
        sender=sender,
        payload=ChatPayload(message=ChatMessage(role=Role.USER, content=[TextBlock(text=prompt)])),
    )


__all__ = ["RunOutcome", "Runtime", "ThreadBusyError"]
