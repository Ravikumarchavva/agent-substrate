"""Worker — the run loop that leases runs and calls Agent.run().

Stage 0 design: each leased run is executed as an asyncio Task.  The Task
stays alive while the agent is awaiting inside ctx.ask() or
ctx.sleep_until_signal() — the coroutine is suspended by asyncio, not by
the durable serialize/fold mechanism (that is Stage 1).

Multiple agents can be in-flight concurrently because each is its own Task.
The Scheduler's lease capacity controls how many are started per poll tick.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from ravi.kernel.agent.runtime_context import CancellationToken, RunMeta
from ravi.kernel.runtime.ids import RunStatus
from ravi.kernel.runtime.log_entry import RunLogEntry
from ravi.kernel.core.errors import CancellationError

if TYPE_CHECKING:
    from ravi.agents.runtime.backends._event_log import InMemoryEventLog
    from ravi.agents.runtime.backends._fanout import PushAllFanout
    from ravi.agents.runtime.backends._follow_graph import InMemoryFollowGraph
    from ravi.agents.runtime.backends._inbox import InMemoryInbox
    from ravi.agents.runtime.backends._journal import InMemoryJournal
    from ravi.agents.runtime.backends._scheduler import InMemoryScheduler
    from ravi.agents.runtime.backends._signal_bus import InMemorySignalBus
    from ravi.agents.runtime.backends._supervisor import InMemorySupervisor
    from ravi.kernel.runtime.agent import Agent

logger = logging.getLogger(__name__)


class Worker:
    """Single-process worker that drives leased runs to completion."""

    POLL_INTERVAL = 0.05  # seconds between queue polls

    def __init__(
        self,
        worker_id: str,
        event_log: InMemoryEventLog,
        journal: InMemoryJournal,
        inbox: InMemoryInbox,
        follow_graph: InMemoryFollowGraph,
        fanout: PushAllFanout,
        scheduler: InMemoryScheduler,
        supervisor: InMemorySupervisor,
        signal_bus: InMemorySignalBus,
        registry: dict,  # AgentId → Agent
    ) -> None:
        self._worker_id = worker_id
        self._event_log = event_log
        self._journal = journal
        self._inbox = inbox
        self._follow_graph = follow_graph
        self._fanout = fanout
        self._scheduler = scheduler
        self._supervisor = supervisor
        self._signal_bus = signal_bus
        self._registry = registry
        self._running = False
        self._poll_task: asyncio.Task | None = None
        self._tokens: dict[str, CancellationToken] = {}  # run_id → token for external cancel
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
        if hasattr(self._scheduler, "_status"):
            self._scheduler._status[run_id] = RunStatus.CANCELLED

        task = self._tasks.get(run_id)
        if task is not None and not task.done():
            task.cancel()
        token = self._tokens.get(run_id)
        if token is not None:
            token.cancel("cancelled-externally")

        if task is None:
            from ravi.kernel.runtime.log_entry import RunLogEntry
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
                await self._supervisor.record_completion(run_id, RunStatus.CANCELLED)
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
            from ravi.agents.tools.toolbox import Toolbox
            registry = Toolbox()
        from ravi.agents.tools.invoker import ToolInvoker
        from ravi.kernel.tools.chain import ChainPolicy
        from ravi.kernel.tools import ToolRisk

        approval = getattr(agent, "approval_handler", None)
        if approval is not None and not hasattr(approval, "request"):
            from ravi.kernel.tools.approval import ApprovalDecision
            class CallbackApprovalHandlerAdapter:
                def __init__(self, callback):
                    self.callback = callback
                async def request(self, req):
                    approved = await self.callback(req.call.name, req.call.arguments)
                    return ApprovalDecision.APPROVED if approved else ApprovalDecision.DENIED
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
        from ravi.agents.runtime.context import RunContext

        run_id = lease.run_id
        token = CancellationToken()
        meta = RunMeta(run_id=run_id, cancellation=token, tenant_id=None)
        self._tokens[run_id] = token

        llm_client = getattr(agent, "model", None)
        tool_invoker = self._build_tool_invoker(agent)

        ctx = RunContext(
            meta=meta,
            event_log=self._event_log,
            journal=self._journal,
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

        # Log run start (seq -1 → first append at seq 0)
        seq = await self._event_log.last_seq(run_id)
        if seq < 0:
            await self._event_log.append(
                run_id,
                RunLogEntry(run_id=run_id, seq=0, kind="run.started"),
                expected_seq=-1,
            )

        # Drain inbox
        inbox_msgs = await self._inbox.drain(agent.id, max=100)

        hooks = getattr(agent, "hooks", None)
        middleware = getattr(agent, "middleware", None)
        if hooks:
            from ravi.agents.hooks.manager import HookEvent
            await hooks.dispatch(HookEvent.RUN_START, {"agent_name": str(agent.id), "run_id": run_id})

        try:
            if middleware is not None:
                await middleware.execute(ctx, lambda c: agent.run(c, inbox_msgs))
            else:
                await agent.run(ctx, inbox_msgs)

            # Ack all processed messages
            for msg in inbox_msgs:
                await self._inbox.ack(agent.id, msg.id)

            # Clean up run-scoped history for transient sub-agents
            session_ids = {msg.correlation_id or run_id for msg in inbox_msgs} or {run_id}
            await self._maybe_clear_run_history(agent, run_id, session_ids=session_ids)

            final_seq = await self._event_log.last_seq(run_id)
            await self._event_log.append(
                run_id,
                RunLogEntry(run_id=run_id, seq=final_seq + 1, kind="run.completed"),
                expected_seq=final_seq,
            )
            await self._scheduler.release(lease, status=RunStatus.COMPLETED)
            await self._supervisor.record_completion(run_id, RunStatus.COMPLETED)

        except (asyncio.CancelledError, CancellationError):
            token.cancel("task-cancelled")
            final_seq = await self._event_log.last_seq(run_id)
            await self._event_log.append(
                run_id,
                RunLogEntry(run_id=run_id, seq=final_seq + 1, kind="run.cancelled"),
                expected_seq=final_seq,
            )
            await self._scheduler.release(lease, status=RunStatus.CANCELLED)
            await self._supervisor.record_completion(run_id, RunStatus.CANCELLED)

        except Exception as exc:
            from ravi.kernel.core.errors import (
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
                payload = {"error": f"Request blocked: {exc.message}", "status": "guardrail_tripped"}  # type: ignore[union-attr]
            elif is_budget:
                payload = {"error": str(exc), "status": "budget_exhausted"}
            else:
                crash = AgentCrashError(str(exc), run_id=run_id, agent_id=agent.id)
                payload = {"error": str(crash), "status": "agent_crashed"}

            await self._event_log.append(
                run_id,
                RunLogEntry(run_id=run_id, seq=final_seq + 1, kind="run.failed", payload=payload),
                expected_seq=final_seq,
            )
            await self._scheduler.release(lease, status=RunStatus.FAILED)
            await self._supervisor.record_completion(run_id, RunStatus.FAILED, error=str(exc))
        finally:
            if hooks:
                from ravi.agents.hooks.manager import HookEvent
                await hooks.dispatch(HookEvent.RUN_END, {"agent_name": str(agent.id), "run_id": run_id})
            self._tokens.pop(run_id, None)
            self._tasks.pop(run_id, None)

    async def _maybe_clear_run_history(
        self, agent: object, run_id: str, *, session_ids: set[str]
    ) -> None:
        """Call clear_run for each session touched in this run if retention is RUN."""
        from ravi.kernel.agent.supervision import HistoryRetention

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
                logger.warning("clear_run failed for agent %s run %s session %s", agent_id, run_id, session_id)
