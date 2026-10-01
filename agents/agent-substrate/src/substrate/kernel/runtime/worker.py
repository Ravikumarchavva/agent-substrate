"""Worker — the run loop that leases runs and calls Agent.run().

Each leased run is executed as an asyncio Task, but the Task does NOT stay
alive across a suspension. When the agent awaits something not yet available
(``ctx.ask()``, ``ctx.sleep_until_signal()``, ``ctx.sleep_until()``,
``ctx.join()``), ``RunContext`` raises ``SuspendInterrupt`` — a
``BaseException`` that unwinds straight out of ``agent.run()`` to
``_run_agent`` below. This Task then genuinely ends: the run is released
with ``status=SUSPENDED`` and costs nothing until something wakes it. Resume
is just a fresh lease: any worker (this one or another) picks it up, folds a
new ``EffectCache`` from the EventLogProtocol, and calls ``agent.run()`` again from
the top — every already-completed effect and consumed signal replays as a
cache hit, so execution fast-forwards silently back to the same wait point,
which now succeeds. See ``agents/runtime/context.py`` module docstring for
the full suspend/resume contract.

Multiple agents can be in-flight concurrently because each is its own Task.
The SchedulerProtocol's lease capacity controls how many are started per poll tick.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from substrate.kernel.runtime.log_entry import RunLogKind
from substrate.kernel.agent.runtime_context import RunMeta
from substrate.agents.runtime.cancellation import CancellationToken
from substrate.kernel.runtime.ids import RunId, RunStatus
from substrate.kernel.runtime.log_entry import RunLogEntry
from substrate.kernel.agent.supervision import Supervision
from substrate.kernel.exceptions import CancellationError, SuspendInterrupt

if TYPE_CHECKING:
    from substrate.agents.runtime.resolver import ActorResolver
    from substrate.kernel.runtime.log_entry import EventLogProtocol
    from substrate.kernel.runtime.inbox import InboxProtocol
    from substrate.agents.runtime._scheduling import SchedulerBackend
    from substrate.kernel.runtime.wakeup import SignalBusProtocol
    from substrate.kernel.runtime.supervisor import SupervisorProtocol
    from substrate.agents.runtime.context import Agent
    from substrate.kernel.runtime.fanout import FanoutStrategy
    from substrate.kernel.runtime.follow_graph import FollowGraph

logger = logging.getLogger(__name__)


class Worker:
    """Single-process worker that drives leased runs to completion."""

    POLL_INTERVAL = 0.05  # seconds between queue polls

    def __init__(
        self,
        worker_id: str,
        event_log: EventLogProtocol,
        inbox: InboxProtocol,
        follow_graph: FollowGraph,
        fanout: FanoutStrategy,
        scheduler: SchedulerBackend,
        supervisor: SupervisorProtocol,
        signal_bus: SignalBusProtocol,
        resolver: ActorResolver,
    ) -> None:
        self._worker_id = worker_id
        self._event_log = event_log
        self._inbox = inbox
        self._follow_graph = follow_graph
        self._fanout = fanout
        self._scheduler = scheduler
        self._supervisor = supervisor
        self._signal_bus = signal_bus
        self._resolver = resolver
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._tokens: dict[
            str, CancellationToken
        ] = {}  # run_id → token for external cancel
        self._tasks: dict[str, asyncio.Task] = {}  # run_id → Task

    async def start(self) -> None:
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop(), name="worker-poll")

    async def stop(self) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        # Cancel all active agent tasks
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel(self, run_id: str) -> None:
        """Cancel a running or pending task by run_id.

        Local fast path only: cancels this worker's own Task/CancellationToken
        if it holds one. If it doesn't, that does NOT mean the run is idle —
        on a durable multi-replica backend it may be actively leased by
        another worker right now. ``cancel_pending()`` atomically checks and
        transitions in one step, so only a genuinely non-running run gets
        force-terminalized here; a RUNNING run is left alone for
        ``SupervisorProtocol.cancel()``'s durable ``cancel_requested`` flag (observed
        by the owning worker's own heartbeat) to handle instead — forcing
        completion here would race that worker's real completion.
        """
        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
        token = self._tokens.get(run_id)
        if token is not None:
            token.cancel("cancelled-externally")

        if task is None and await self._scheduler.cancel_pending(run_id):
            from substrate.kernel.runtime.log_entry import RunLogEntry

            try:
                seq = await self._event_log.last_seq(run_id)
                # If run.started was not even logged yet, make sure we sequence it properly
                if seq < 0:
                    await self._event_log.append(
                        run_id,
                        RunLogEntry(run_id=run_id, seq=0, kind=RunLogKind.RUN_STARTED),
                        expected_seq=-1,
                    )
                    seq = 0
                await self._event_log.append(
                    run_id,
                    RunLogEntry(
                        run_id=run_id,
                        seq=seq + 1,
                        kind=RunLogKind.RUN_CANCELLED,
                        payload={"reason": "cancelled-externally"},
                    ),
                    expected_seq=seq,
                )
            except Exception:
                pass
            try:
                await self._supervisor.finish_run(run_id, RunStatus.CANCELLED)
            except Exception:
                pass

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                leases = await self._scheduler.lease(
                    worker_id=self._worker_id, capacity=10
                )
                for lease in leases:
                    agent = await self._resolver.resolve(lease.agent_id)
                    if agent is None:
                        # No live instance and no factory for this type (e.g.
                        # startup cold-resume race, before registration runs).
                        # Hold the lease and skip — it expires after 30 s, at which
                        # point the run is reclaimed as pending and retried once the
                        # resume hook has registered the agent.
                        logger.warning(
                            "Agent %s not in registry — holding lease for resume",
                            lease.agent_id,
                        )
                        continue
                    task = asyncio.create_task(
                        self._run_agent(lease, agent),
                        name=f"run-{lease.run_id[:8]}",
                    )
                    self._tasks[lease.run_id] = task
            except Exception:
                logger.exception("Worker poll error")
            await asyncio.sleep(self.POLL_INTERVAL)

    def _build_tool_invoker(self, agent: Agent):
        """Build a ToolInvoker from the agent's declared tools, if any."""
        registry = getattr(agent, "tools", None)
        if registry is None:
            from substrate.agents.tools.toolbox import Toolbox

            registry = Toolbox()
        from substrate.agents.tools.invoker import DIRECT_CALL_POLICY, ToolInvoker
        from substrate.kernel.tools import ToolRisk

        approval = getattr(agent, "approval_handler", None)
        if approval is not None and not hasattr(approval, "request"):
            from substrate.kernel.tools.approval import ApprovalDecision, ApprovalResult

            class CallbackApprovalHandlerAdapter:
                """Adapts a bare ``async def callback(name, args) -> bool`` into
                the kernel ``ApprovalHandler`` Protocol — for callers that hand
                ``ReActAgent(approval_handler=...)`` a plain approve/deny
                callback rather than a full Protocol implementation. Can only
                ever produce APPROVED/DENIED — a bare bool has no way to
                express MODIFIED."""

                def __init__(self, callback):
                    self.callback = callback

                async def request(self, req):
                    approved = await self.callback(req.call.name, req.call.arguments)
                    return ApprovalResult(
                        decision=ApprovalDecision.APPROVED
                        if approved
                        else ApprovalDecision.DENIED
                    )

            approval = CallbackApprovalHandlerAdapter(approval)

        blob_store = getattr(agent, "blob_store", None)
        policy = getattr(agent, "tool_policy", None) or DIRECT_CALL_POLICY
        hooks = getattr(agent, "hooks", None)

        req_risk = getattr(agent, "approval_required_risk", None)
        if req_risk is not None:
            if req_risk == ToolRisk.CRITICAL:
                max_unapproved = ToolRisk.HIGH
            elif req_risk == ToolRisk.HIGH:
                max_unapproved = ToolRisk.SAFE
            else:
                max_unapproved = ToolRisk.SAFE
            policy = policy.model_copy(update={"max_risk_unapproved": max_unapproved})

        return ToolInvoker(
            registry=registry,
            approval_handler=approval,
            artifact_store=blob_store,
            policy=policy,
            hooks=hooks,
        )

    async def _resolve_deadline(
        self, run_id: RunId, supervision: Supervision | None
    ) -> datetime | None:
        """Resolve ``supervision.execution_budget.deadline_s`` into an absolute
        cutoff, enforced by ``RunMeta.check()`` at every ``ctx.check()`` call.

        Anchored to the run's true start (the ``ts`` of its ``run.started``
        log entry, seq 0), not "now" on this lease — a resumed run must not
        get its deadline pushed out on every re-lease, or the budget would
        never actually expire. On the very first lease the log has no
        entries yet (``run.started`` is logged from inside ``agent.run()``,
        after this method runs), so "now" genuinely *is* the run's start in
        that one case.
        """
        budget = supervision.execution_budget if supervision else None
        if budget is None or budget.deadline_s is None:
            return None
        started_at = None
        async for entry in self._event_log.read(run_id, from_seq=0):
            started_at = entry.ts
            break
        if started_at is None:
            started_at = datetime.now(timezone.utc)
        return started_at + timedelta(seconds=budget.deadline_s)

    async def _run_agent(self, lease, agent: Agent) -> None:
        from substrate.agents.runtime.context import RunContext
        from substrate.agents.runtime.effect_cache import EffectCache

        run_id = lease.run_id
        token = CancellationToken()
        # Populate tenant and supervision hierarchy (inheriting parent budgets)
        supervision = await self._supervisor.supervision_of(run_id)
        deadline = await self._resolve_deadline(run_id, supervision)
        meta = RunMeta(
            run_id=run_id,
            cancellation=token,
            tenant_id=lease.tenant,
            supervision=supervision,
            deadline=deadline,
        )
        self._tokens[run_id] = token

        llm_client = getattr(agent, "model", None)
        tool_invoker = self._build_tool_invoker(agent)
        blob_store = getattr(agent, "blob_store", None)

        # Fold recorded effect.result entries into the effect cache for replay
        effect_cache = await EffectCache.fold(self._event_log, run_id)

        ctx = RunContext(
            meta=meta,
            event_log=self._event_log,
            effect_cache=effect_cache,
            blob_store=blob_store,
            inbox=self._inbox,
            follow_graph=self._follow_graph,
            fanout=self._fanout,
            scheduler=self._scheduler,
            supervisor=self._supervisor,
            signal_bus=self._signal_bus,
            llm_client=llm_client,
            tool_invoker=tool_invoker,
            agent=agent,
        )

        # Log initial run start via ctx._log to preserve seq cursor
        if effect_cache.last_seq < 0:
            await ctx._log(RunLogKind.RUN_STARTED, {})

        # Journaled inbox drain: preserve exact drained message IDs across replays
        from substrate.kernel.runtime.effects import Effect

        drain_path = ctx._alloc_path()
        drain_effect_id = Effect.make_id(run_id, drain_path, "inbox.drain", {})
        cached_drain = ctx._lookup_effect(drain_effect_id)
        if cached_drain is not None:
            drained = await ctx._resolve_effect_value(cached_drain)
            all_msgs = await self._inbox.drain(agent.id, max=1000)
            by_id = {m.id: m for m in all_msgs}
            inbox_msgs = [
                by_id[mid] for mid in drained.get("msg_ids", []) if mid in by_id
            ]
        else:
            inbox_msgs = await self._inbox.drain(agent.id, max=100)
            await ctx._record_effect(
                drain_effect_id, "ok", {"msg_ids": [m.id for m in inbox_msgs]}
            )

        hooks = getattr(agent, "hooks", None)
        if hooks:
            from substrate.agents.hooks.manager import HookEvent

            await hooks.dispatch(
                HookEvent.RUN_START, {"agent_name": str(agent.id), "run_id": run_id}
            )

        # Keep the Postgres lease alive for long-running agents (LLM calls can
        # easily exceed the 30-second default lease).  The heartbeat runs every
        # Periodic heartbeat maintains the lease during long-running operations
        _HEARTBEAT_INTERVAL = 15

        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                try:
                    cancel_requested = await self._scheduler.heartbeat(lease)
                    if cancel_requested:
                        # Cancellation requested out-of-band by supervisor/admin
                        token.cancel("cancel_requested")
                except Exception:
                    pass  # never let a missed heartbeat kill the run

        heartbeat_task = asyncio.create_task(_heartbeat(), name=f"hb-{run_id[:8]}")
        try:
            await agent.run(ctx, inbox_msgs)

            # Ack all processed messages
            for msg in inbox_msgs:
                await self._inbox.ack(agent.id, msg.id)

            # Clean up run-scoped history for transient sub-agents
            session_ids = {msg.correlation_id or run_id for msg in inbox_msgs} or {
                run_id
            }
            await self._maybe_clear_run_history(agent, run_id, session_ids=session_ids)

            final_seq = await self._event_log.last_seq(run_id)
            await self._event_log.append(
                run_id,
                RunLogEntry(run_id=run_id, seq=final_seq + 1, kind=RunLogKind.RUN_COMPLETED),
                expected_seq=final_seq,
            )
            await self._scheduler.release(lease, status=RunStatus.COMPLETED)
            await self._supervisor.finish_run(run_id, RunStatus.COMPLETED)

        except SuspendInterrupt as exc:
            # Genuine dormancy: messages remain unacked for replay; release with wake condition
            await self._scheduler.release(
                lease, status=RunStatus.SUSPENDED, wake_on=exc.wakeup
            )

        except (asyncio.CancelledError, CancellationError):
            token.cancel("task-cancelled")
            final_seq = await self._event_log.last_seq(run_id)
            await self._event_log.append(
                run_id,
                RunLogEntry(run_id=run_id, seq=final_seq + 1, kind=RunLogKind.RUN_CANCELLED),
                expected_seq=final_seq,
            )
            await self._scheduler.release(lease, status=RunStatus.CANCELLED)
            await self._supervisor.finish_run(run_id, RunStatus.CANCELLED)

        except Exception as exc:
            from substrate.kernel.exceptions import (
                AgentCrashError,
                BudgetExhaustedError,
                MiddlewareTermination,
                PermanentError,
            )

            is_guardrail = isinstance(exc, MiddlewareTermination)
            is_budget = isinstance(exc, BudgetExhaustedError)
            is_permanent = isinstance(exc, PermanentError)
            is_crash = not is_guardrail and not is_budget and not is_permanent
            # Deterministic errors (guardrails, budgets, permanent errors) skip retries
            retryable = not (is_guardrail or is_budget or is_permanent)

            if is_crash:
                logger.exception("Agent %s run %s crashed", agent.id, run_id)
            else:
                logger.warning("Agent %s run %s stopped: %s", agent.id, run_id, exc)

            if is_guardrail or is_budget or is_permanent:
                for msg in inbox_msgs:
                    await self._inbox.ack(agent.id, msg.id)
            else:
                for msg in inbox_msgs:
                    await self._inbox.nack(agent.id, msg.id, error=str(exc))

            # Atomically decide retry-vs-terminal before emitting run.failed
            terminal = await self._scheduler.release(
                lease, status=RunStatus.FAILED, retryable=retryable
            )
            if terminal:
                if is_guardrail:
                    guardrail_msg = getattr(exc, "message", None) or str(exc)
                    payload = {
                        "error": f"Request blocked: {guardrail_msg}",
                        "status": "guardrail_tripped",
                    }
                elif is_budget:
                    payload = {"error": str(exc), "status": "budget_exhausted"}
                elif is_permanent:
                    payload = {"error": str(exc), "status": "permanent_error"}
                else:
                    crash = AgentCrashError(str(exc), run_id=run_id, agent_id=agent.id)
                    payload = {"error": str(crash), "status": "agent_crashed"}

                final_seq = await self._event_log.last_seq(run_id)
                await self._event_log.append(
                    run_id,
                    RunLogEntry(
                        run_id=run_id,
                        seq=final_seq + 1,
                        kind=RunLogKind.RUN_FAILED,
                        payload=payload,
                    ),
                    expected_seq=final_seq,
                )
                await self._supervisor.finish_run(
                    run_id, RunStatus.FAILED, error=str(exc)
                )
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            if hooks:
                from substrate.agents.hooks.manager import HookEvent

                await hooks.dispatch(
                    HookEvent.RUN_END, {"agent_name": str(agent.id), "run_id": run_id}
                )
            self._tokens.pop(run_id, None)
            self._tasks.pop(run_id, None)

    async def _maybe_clear_run_history(
        self, agent: object, run_id: str, *, session_ids: set[str]
    ) -> None:
        """Delete each session touched in this run if retention is RUN (run-scoped history)."""
        from substrate.kernel.agent.supervision import HistoryRetention

        context_cfg = getattr(agent, "_context", None)
        if context_cfg is None:
            return
        retention = getattr(context_cfg, "retention", HistoryRetention.PERMANENT)
        if retention != HistoryRetention.RUN:
            return

        history = getattr(context_cfg, "history", None)
        if history is None:
            return

        agent_id = getattr(agent, "id", None)
        for session_id in session_ids:
            try:
                await history.delete_session(session_id)
            except Exception:
                logger.warning(
                    "delete_session failed for agent %s run %s session %s",
                    agent_id,
                    run_id,
                    session_id,
                )
