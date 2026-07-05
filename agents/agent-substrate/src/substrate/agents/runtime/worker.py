"""Worker — the run loop that leases runs and calls Agent.run().

Each leased run is executed as an asyncio Task, but the Task does NOT stay
alive across a suspension. When the agent awaits something not yet available
(``ctx.ask()``, ``ctx.sleep_until_signal()``, ``ctx.sleep_until()``,
``ctx.join()``), ``RunContext`` raises ``SuspendInterrupt`` — a
``BaseException`` that unwinds straight out of ``agent.run()`` to
``_run_agent`` below. This Task then genuinely ends: the run is released
with ``status=SUSPENDED`` and costs nothing until something wakes it. Resume
is just a fresh lease: any worker (this one or another) picks it up, folds a
new ``EffectCache`` from the EventLog, and calls ``agent.run()`` again from
the top — every already-completed effect and consumed signal replays as a
cache hit, so execution fast-forwards silently back to the same wait point,
which now succeeds. See ``agents/runtime/context.py`` module docstring for
the full suspend/resume contract.

Multiple agents can be in-flight concurrently because each is its own Task.
The Scheduler's lease capacity controls how many are started per poll tick.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from substrate.kernel.agent.runtime_context import RunMeta
from substrate.agents.runtime.cancellation import CancellationToken
from substrate.kernel.runtime.ids import RunStatus
from substrate.kernel.runtime.log_entry import RunLogEntry
from substrate.kernel.core.errors import CancellationError, SuspendInterrupt

if TYPE_CHECKING:
    from substrate.kernel.runtime.log_entry import EventLog
    from substrate.kernel.runtime.inbox import Inbox
    from substrate.kernel.runtime.scheduler import Scheduler
    from substrate.kernel.runtime.wakeup import SignalBus
    from substrate.kernel.runtime.supervisor import Supervisor
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
        event_log: EventLog,
        inbox: Inbox,
        follow_graph: FollowGraph,
        fanout: FanoutStrategy,
        scheduler: Scheduler,
        supervisor: Supervisor,
        signal_bus: SignalBus,
        registry: dict,  # AgentId → Agent
    ) -> None:
        self._worker_id = worker_id
        self._event_log = event_log
        self._inbox = inbox
        self._follow_graph = follow_graph
        self._fanout = fanout
        self._scheduler = scheduler
        self._supervisor = supervisor
        self._signal_bus = signal_bus
        self._registry = registry
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
        """Cancel a running or pending task by run_id."""
        status_map = getattr(self._scheduler, "_status", None)
        if status_map is not None:
            status_map[run_id] = RunStatus.CANCELLED

        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
        token = self._tokens.get(run_id)
        if token is not None:
            token.cancel("cancelled-externally")

        if task is None:
            from substrate.kernel.runtime.log_entry import RunLogEntry

            try:
                seq = await self._event_log.last_seq(run_id)
                # If run.started was not even logged yet, make sure we sequence it properly
                if seq < 0:
                    await self._event_log.append(
                        run_id,
                        RunLogEntry(run_id=run_id, seq=0, kind="run.started"),
                        expected_seq=-1,
                    )
                    seq = 0
                await self._event_log.append(
                    run_id,
                    RunLogEntry(
                        run_id=run_id,
                        seq=seq + 1,
                        kind="run.cancelled",
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
                    agent = self._registry.get(lease.agent_id)
                    if agent is None:
                        # Agent not yet registered (e.g. startup cold-resume race).
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
        from substrate.agents.tools.invoker import ToolInvoker
        from substrate.kernel.tools.chain import ChainPolicy
        from substrate.kernel.tools import ToolRisk

        approval = getattr(agent, "approval_handler", None)
        if approval is not None and not hasattr(approval, "request"):
            from substrate.kernel.tools.approval import ApprovalDecision

            class CallbackApprovalHandlerAdapter:
                def __init__(self, callback):
                    self.callback = callback

                async def request(self, req):
                    approved = await self.callback(req.call.name, req.call.arguments)
                    return (
                        ApprovalDecision.APPROVED
                        if approved
                        else ApprovalDecision.DENIED
                    )

            approval = CallbackApprovalHandlerAdapter(approval)

        blob_store = getattr(agent, "blob_store", None)
        policy = getattr(agent, "tool_policy", None) or ChainPolicy()
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

    async def _run_agent(self, lease, agent: Agent) -> None:
        from substrate.agents.runtime.context import RunContext
        from substrate.agents.runtime.effect_cache import EffectCache

        run_id = lease.run_id
        token = CancellationToken()
        # lease.tenant is whatever Runtime.submit(..., tenant=...) passed to
        # enqueue() — "default" if the caller never set one. Previously
        # always None here regardless of what was enqueued: RunMeta.tenant_id
        # existed end-to-end (kernel type, request path) but nothing ever
        # actually populated it on the resume/execute side.
        #
        # supervision_of() is None for a top-level submit() (never spawned)
        # or an in-memory run before any spawn happened to persist one — in
        # both cases ctx.spawn() falls back to Supervision.root() exactly as
        # it always has. For a run that WAS itself ctx.spawn()'d, this is
        # what lets its own ctx.spawn() calls inherit the caller's
        # execution_budget via Supervision.spawn_child() instead of handing
        # every grandchild a fresh, unlimited budget unrelated to whatever
        # constraints its own parent was given.
        supervision = await self._supervisor.supervision_of(run_id)
        meta = RunMeta(
            run_id=run_id,
            cancellation=token,
            tenant_id=lease.tenant,
            supervision=supervision,
        )
        self._tokens[run_id] = token

        llm_client = getattr(agent, "model", None)
        tool_invoker = self._build_tool_invoker(agent)
        blob_store = getattr(agent, "blob_store", None)

        # Fold the EventLog into the effect cache once per lease — this is
        # the "replay" half of fold-is-truth: every effect.result this run
        # already recorded becomes a free in-memory lookup for the rest of
        # this invocation. last_seq also seeds RunContext's local seq
        # cursor, so no separate last_seq() query is needed below.
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

        # Log run start (last_seq -1 → first append at seq 0). Routed through
        # ctx._log so its local seq cursor stays the single source of truth
        # instead of drifting from an out-of-band append.
        if effect_cache.last_seq < 0:
            await ctx._log("run.started", {})

        # Journaled drain: inbox.drain() is a non-destructive peek (messages
        # stay until acked), so on a genuine replay a message that arrived
        # DURING the prior attempt's suspension would otherwise be silently
        # folded into inbox_msgs this time, changing what the agent sees
        # from one replay attempt to the next — a real nondeterminism source.
        # Recording which message ids were drained on the live attempt and
        # reusing that exact set on replay closes it; a message that arrives
        # mid-suspension simply waits for the run's NEXT drain instead.
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
        # _HEARTBEAT_INTERVAL seconds; InMemoryScheduler.heartbeat is a no-op.
        _HEARTBEAT_INTERVAL = 15

        async def _heartbeat() -> None:
            while True:
                await asyncio.sleep(_HEARTBEAT_INTERVAL)
                try:
                    cancel_requested = await self._scheduler.heartbeat(lease)
                    if cancel_requested:
                        # Durable cancel or deadline observed for this run —
                        # possibly requested by a DIFFERENT worker process
                        # (Supervisor.cancel() has no reference to this
                        # process's live Task), so the heartbeat round-trip
                        # is how it reaches this token. ctx.check() picks it
                        # up cooperatively at the next yield point.
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
                RunLogEntry(run_id=run_id, seq=final_seq + 1, kind="run.completed"),
                expected_seq=final_seq,
            )
            await self._scheduler.release(lease, status=RunStatus.COMPLETED)
            await self._supervisor.finish_run(run_id, RunStatus.COMPLETED)

        except SuspendInterrupt as exc:
            # Genuine dormancy: no ack/nack (the drained messages are still
            # unacked in the inbox — see the journaled drain above — and will
            # be there, exactly as-is, on the next lease), no terminal event,
            # no finish_run. release(SUSPENDED) is the only state
            # change; the Task ends here and the run costs nothing until
            # something wakes it.
            await self._scheduler.release(
                lease, status=RunStatus.SUSPENDED, wake_on=exc.wakeup
            )

        except (asyncio.CancelledError, CancellationError):
            token.cancel("task-cancelled")
            final_seq = await self._event_log.last_seq(run_id)
            await self._event_log.append(
                run_id,
                RunLogEntry(run_id=run_id, seq=final_seq + 1, kind="run.cancelled"),
                expected_seq=final_seq,
            )
            await self._scheduler.release(lease, status=RunStatus.CANCELLED)
            await self._supervisor.finish_run(run_id, RunStatus.CANCELLED)

        except Exception as exc:
            from substrate.kernel.core.errors import (
                AgentCrashError,
                BudgetExhaustedError,
                MiddlewareTermination,
            )

            is_guardrail = isinstance(exc, MiddlewareTermination)
            is_budget = isinstance(exc, BudgetExhaustedError)
            is_crash = not is_guardrail and not is_budget

            if is_crash:
                logger.exception("Agent %s run %s crashed", agent.id, run_id)
            else:
                logger.warning("Agent %s run %s stopped: %s", agent.id, run_id, exc)

            if is_guardrail or is_budget:
                for msg in inbox_msgs:
                    await self._inbox.ack(agent.id, msg.id)
            else:
                for msg in inbox_msgs:
                    await self._inbox.nack(agent.id, msg.id, error=str(exc))

            final_seq = await self._event_log.last_seq(run_id)
            if is_guardrail:
                payload = {
                    "error": f"Request blocked: {exc.message}",
                    "status": "guardrail_tripped",
                }  # type: ignore[union-attr]
            elif is_budget:
                payload = {"error": str(exc), "status": "budget_exhausted"}
            else:
                crash = AgentCrashError(str(exc), run_id=run_id, agent_id=agent.id)
                payload = {"error": str(crash), "status": "agent_crashed"}

            await self._event_log.append(
                run_id,
                RunLogEntry(
                    run_id=run_id, seq=final_seq + 1, kind="run.failed", payload=payload
                ),
                expected_seq=final_seq,
            )
            await self._scheduler.release(lease, status=RunStatus.FAILED)
            await self._supervisor.finish_run(run_id, RunStatus.FAILED, error=str(exc))
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
        """Call clear_run for each session touched in this run if retention is RUN."""
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
                await history.clear_run(agent_id, session_id=session_id, run_id=run_id)
            except Exception:
                logger.warning(
                    "clear_run failed for agent %s run %s session %s",
                    agent_id,
                    run_id,
                    session_id,
                )
