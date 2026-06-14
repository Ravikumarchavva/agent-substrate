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

from ravi.kernel.runtime.ids import RunStatus
from ravi.kernel.runtime.log_entry import RunLogEntry

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

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                leases = await self._scheduler.lease(
                    worker_id=self._worker_id, capacity=10
                )
                for lease in leases:
                    agent_id = self._scheduler.agent_for(lease.run_id)
                    if agent_id is None:
                        logger.warning("No agent registered for run %s", lease.run_id)
                        await self._scheduler.release(lease, status=RunStatus.FAILED)
                        continue
                    agent = self._registry.get(agent_id)
                    if agent is None:
                        logger.warning("Agent %s not in registry", agent_id)
                        await self._scheduler.release(lease, status=RunStatus.FAILED)
                        continue
                    asyncio.create_task(
                        self._run_agent(lease, agent),
                        name=f"run-{lease.run_id[:8]}",
                    )
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
        )

    async def _run_agent(self, lease, agent: Agent) -> None:
        from ravi.agents.runtime.context import RunContext

        run_id = lease.run_id
        llm_client = getattr(agent, "model", None)
        tool_invoker = self._build_tool_invoker(agent)

        ctx = RunContext(
            run_id=run_id,
            tenant_id=None,
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

        try:
            await agent.run(ctx, inbox_msgs)

            # Ack all processed messages
            for msg in inbox_msgs:
                await self._inbox.ack(agent.id, msg.id)

            final_seq = await self._event_log.last_seq(run_id)
            await self._event_log.append(
                run_id,
                RunLogEntry(run_id=run_id, seq=final_seq + 1, kind="run.completed"),
                expected_seq=final_seq,
            )
            await self._scheduler.release(lease, status=RunStatus.COMPLETED)

        except asyncio.CancelledError:
            final_seq = await self._event_log.last_seq(run_id)
            await self._event_log.append(
                run_id,
                RunLogEntry(run_id=run_id, seq=final_seq + 1, kind="run.cancelled"),
                expected_seq=final_seq,
            )
            await self._scheduler.release(lease, status=RunStatus.CANCELLED)

        except Exception as exc:
            from ravi.kernel.core.errors import MiddlewareTermination, BudgetExhaustedError
            is_guardrail = isinstance(exc, MiddlewareTermination)
            is_max_iter = isinstance(exc, BudgetExhaustedError)
            logger.exception("Agent %s run %s failed", agent.id, run_id)
            if is_guardrail or is_max_iter:
                for msg in inbox_msgs:
                    await self._inbox.ack(agent.id, msg.id)
            else:
                for msg in inbox_msgs:
                    await self._inbox.nack(agent.id, msg.id, error=str(exc))
            final_seq = await self._event_log.last_seq(run_id)
            payload = {}
            if is_guardrail:
                payload["error"] = f"Request blocked: {exc.message}"
                payload["status"] = "guardrail_tripped"
            elif is_max_iter:
                payload["error"] = str(exc)
                payload["status"] = "max_iterations"
            else:
                payload["error"] = str(exc)
            await self._event_log.append(
                run_id,
                RunLogEntry(
                    run_id=run_id,
                    seq=final_seq + 1,
                    kind="run.failed",
                    payload=payload,
                ),
                expected_seq=final_seq,
            )
            await self._scheduler.release(lease, status=RunStatus.FAILED)
