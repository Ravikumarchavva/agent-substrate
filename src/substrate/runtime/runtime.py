"""Runtime — the durable engine, over a ``Store``.

::

    async with Runtime.open("./.substrate") as rt:
        await rt.register(agent)
        outcome = await rt.run(agent, "What is the capital of France?")

A runtime owns a worker that leases runs from the store and executes them. Everything it needs to survive a
restart is in the store, so a run submitted before a crash is picked up after one.

``Runtime.open(folder)`` opens a store of its own and closes it on exit. To share one store with other users
of it — threads, memory — connect first and pass it in: ``Runtime(store)``; the runtime then leaves closing
to whoever connected.

Every engine behaviour (retries, leases, replay, supervision) lives here and not in the store, so it is the same
wherever the store keeps its data.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from substrate.types.supervision import Priority
from substrate.types.content import ChatMessage, Role, TextBlock
from substrate.types.identity import Actor, Topic
from substrate.types.trace import TraceContext
from substrate.types.errors import ThreadBusyError
from substrate.types.ids import new_id
from substrate.runtime.message import ChatPayload, Message
from substrate.types.run_status import RunId, RunStatus
from substrate.types.run_log import RunLogEntry, RunLogKind
from substrate.runtime.scheduler import RunRetryPolicy
from substrate.runtime.store import Delivery, RunRecord, RunSpec, RuntimeStore
from substrate.runtime.resolver import ActorFactory, ActorResolver
from substrate.runtime.tail import tail
from substrate.runtime.worker import Worker
from substrate.stores import Store
from substrate.tools.approval import ApprovalDecision, approval_signal

if TYPE_CHECKING:
    from substrate.agents.orchestrator import SubAgentConfig

_TERMINAL_KINDS = frozenset(
    {
        RunLogKind.RUN_COMPLETED,
        RunLogKind.RUN_FAILED,
        RunLogKind.RUN_CANCELLED,
        RunLogKind.RUN_TRUNCATED,
    }
)


@dataclass(frozen=True, slots=True)
class PendingApproval:
    """A tool call a run is waiting for a person to decide (``Runtime.pending_approvals``)."""

    run_id: str
    request_id: str
    tool_name: str
    args: dict[str, Any]
    risk: str
    summary: str = ""


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
        store: Store | RuntimeStore,
        *,
        resolver: ActorResolver | None = None,
        max_concurrency: int = 10,
        lease_s: float = 30.0,
        poll_interval_s: float = 0.05,
    ) -> None:
        self._source = store if isinstance(store, Store) else None
        self._owns_source = False
        if isinstance(store, Store):
            from substrate.runtime.persistence.store import DurableRuntimeStore

            store = DurableRuntimeStore(store.database)
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
    def open(cls, folder: str | Path = "./.substrate", **options: Any) -> Runtime:
        """A runtime on the store in ``folder`` — created if there is none — which it closes when it stops."""
        runtime = cls(Store.at(folder), **options)
        runtime._owns_source = True
        return runtime

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
        await self._resolver.resolve(
            agent_id
        )  # an unknown agent is refused here, not after a lease
        run = await self._store.create_run(
            RunSpec(
                agent=agent_id,
                tenant=tenant,
                thread_id=thread_id,
                priority=priority,
                retry_policy=retry_policy or RunRetryPolicy(max_retries=max_retries),
                trace=TraceContext.new(),
                recipe=recipe,
            ),
            deliveries=[Delivery(agent=agent_id, msg=msg, tenant=tenant)],
        )
        return run.run_id

    async def run(
        self,
        agent: Any,
        prompt: str,
        *,
        tenant: str = "default",
        thread: str | None = None,
    ) -> RunOutcome:
        """Run an agent on one prompt and wait for its answer.

        Each call is a conversation of its own unless you name a ``thread``: runs on the same thread share
        its history, so the agent sees what was said before — across runs, restarts and processes — and a
        thread has one active run at a time (a second raises ``ThreadBusyError``).

        For streaming, several messages, or runs that suspend, use ``register`` +
        ``submit`` and read ``tail`` yourself.
        """
        await self.register(agent)
        message = _chat(Actor(type="user", key="run"), agent.id, prompt)
        if thread is not None:
            message = message.model_copy(update={"correlation_id": thread})
        run_id = await self.submit(
            agent.id, message, tenant=tenant, max_retries=0, thread_id=thread
        )
        text = ""
        async for entry in self.tail(run_id):
            payload = entry.payload or {}
            if entry.kind == RunLogKind.TOOL_RESULT:
                text = ""  # a new turn: the answer is the last assistant message after the final tool result
            elif entry.kind == RunLogKind.ASSISTANT_MESSAGE:
                text = payload.get("text", "")
            elif entry.kind == RunLogKind.RUN_COMPLETED:
                return RunOutcome(
                    run_id=run_id, status=RunStatus.COMPLETED, output=text or None
                )
            elif entry.kind == RunLogKind.RUN_FAILED:
                return RunOutcome(
                    run_id=run_id,
                    status=RunStatus.FAILED,
                    error=payload.get("error", "agent run failed"),
                )
            elif entry.kind == RunLogKind.RUN_CANCELLED:
                return RunOutcome(run_id=run_id, status=RunStatus.CANCELLED)
        return RunOutcome(
            run_id=run_id, status=RunStatus.COMPLETED, output=text or None
        )

    async def ask(
        self,
        agent: Any,
        prompt: str,
        *,
        timeout: float = 120.0,
        tenant: str = "default",
    ) -> RunOutcome:
        """Run an agent that answers with ``ctx.reply`` (a flow, say) and wait for the reply.

        Such agents do not stream text, so ``run`` cannot capture their output. This
        mirrors how one agent calls another: the message carries a ``reply_to`` and the
        reply is read off the signal buffer.
        """
        await self.register(agent)
        sentinel = RunId(new_id())
        msg = _chat(Actor(type="user", key="ask"), agent.id, prompt).model_copy(
            update={"reply_to": sentinel}
        )
        run_id = await self.submit(agent.id, msg, tenant=tenant, max_retries=0)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            payload = await self._store.consume(
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

    async def follow(self, follower: Actor, topic_type: str, topic_source: str) -> None:
        await self._store.follow(follower, Topic(f"{topic_type}/{topic_source}"))

    async def publish(self, topic_type: str, topic_source: str, msg: Message) -> None:
        """Deliver ``msg`` to every follower of the topic."""
        for follower in await self._store.followers_of(
            Topic(f"{topic_type}/{topic_source}")
        ):
            await self._store.deliver(Delivery(agent=follower, msg=msg))

    # ------------------------------------------------------------------ observing

    @property
    def store(self) -> RuntimeStore:
        return self._store

    def tail(
        self, run_id: RunId | str, *, from_seq: int = 0
    ) -> AsyncIterator[RunLogEntry]:
        """A run's entries — live output included — as they happen. Never ends on its own:
        stop iterating when you see the terminal entry you care about."""
        return tail(self._store, run_id, from_seq=from_seq)

    async def read(
        self, run_id: RunId | str, *, from_seq: int = 0
    ) -> list[RunLogEntry]:
        """A run's durable entries so far (no live output): what a replay would see."""
        return await self._store.read_events(
            RunId(run_id), from_seq=from_seq, durable_only=True
        )

    async def get_run(self, run_id: RunId | str) -> RunRecord | None:
        return await self._store.get_run(RunId(run_id))

    async def active_run_for_thread(self, thread_id: str) -> RunRecord | None:
        found = await self._store.find_runs(thread_id=thread_id)
        return found[0] if found else None

    async def runs_for_thread(self, thread_id: str) -> list[RunRecord]:
        """Every run a thread has had, oldest first: the basis for projecting its conversation."""
        return await self._store.find_runs(thread_id=thread_id, active_only=False)

    # ------------------------------------------------------------------ control

    async def cancel(
        self, run_id: RunId | str, *, reason: str = "cancelled"
    ) -> list[RunId]:
        """Cancel a run and everything it spawned. A run on this worker is interrupted
        now; one on another worker notices at its next heartbeat."""
        affected = await self._store.request_cancel(RunId(run_id), reason=reason)
        for affected_id in affected:
            self._worker.cancel_local(str(affected_id), reason)
        return affected

    # ------------------------------------------------------------------ human approval

    async def pending_approvals(self, run_id: RunId | str) -> list[PendingApproval]:
        """The approvals a run is waiting on: requested in its journal, not yet decided."""
        requested: dict[str, PendingApproval] = {}
        for entry in await self.read(run_id):
            if entry.kind == RunLogKind.APPROVAL_REQUESTED:
                payload = entry.payload
                requested[payload["request_id"]] = PendingApproval(
                    run_id=str(run_id),
                    request_id=payload["request_id"],
                    tool_name=payload["tool_name"],
                    args=payload["args"],
                    risk=payload["risk"],
                    summary=payload.get("summary", ""),
                )
            elif entry.kind == RunLogKind.APPROVAL_DECIDED:
                requested.pop(entry.payload["request_id"], None)
        return list(requested.values())

    async def decide(
        self,
        run_id: RunId | str,
        request_id: str,
        decision: ApprovalDecision,
        *,
        by: str,
        reason: str | None = None,
        modified_args: dict[str, Any] | None = None,
    ) -> None:
        """Answer an approval a run is waiting on (``DurableApproval``), from any process on the same store.

        ``by`` is journaled with the decision — an approval nobody can be held to is not a control. ``MODIFIED`` runs the
        call with ``modified_args`` instead of what the model asked for.
        """
        action = {
            ApprovalDecision.APPROVED: "approve",
            ApprovalDecision.DENIED: "deny",
            ApprovalDecision.MODIFIED: "modify",
        }.get(ApprovalDecision(decision))
        if action is None:
            raise ValueError(f"{decision!r} is not a decision a person can make")
        if action == "modify" and modified_args is None:
            raise ValueError("a MODIFIED decision needs modified_args")
        payload: dict[str, Any] = {
            "action": action,
            "decided_by": by,
            "reason": reason,
            "decided_at": datetime.now(tz=timezone.utc).isoformat(),
        }
        if modified_args is not None:
            payload["modified_arguments"] = modified_args
        await self._store.signal(RunId(run_id), approval_signal(request_id), payload)

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        if self._started:
            return
        if self._source is not None:
            await self._source.start()
        await self._store.start()
        await self._worker.start()
        self._started = True

    async def stop(self) -> None:
        if not self._started:
            return
        await self._worker.stop()
        await self._store.aclose()
        if self._owns_source and self._source is not None:
            await self._source.aclose()
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
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=prompt)])
        ),
    )


__all__ = ["RunOutcome", "Runtime", "ThreadBusyError"]
