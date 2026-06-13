"""Worker — the run loop that leases runs and calls DurableAgent.run().

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
    from ravi.agents.runtime.durable._event_log import InMemoryEventLog
    from ravi.agents.runtime.durable._fanout import PushAllFanout
    from ravi.agents.runtime.durable._follow_graph import InMemoryFollowGraph
    from ravi.agents.runtime.durable._inbox import InMemoryInbox
    from ravi.agents.runtime.durable._journal import InMemoryJournal
    from ravi.agents.runtime.durable._scheduler import InMemoryScheduler
    from ravi.agents.runtime.durable._signal_bus import InMemorySignalBus
    from ravi.agents.runtime.durable._supervisor import InMemorySupervisor
    from ravi.kernel.runtime.agent import DurableAgent

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
        registry: dict,  # AgentId → DurableAgent
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

    async def _run_agent(self, lease, agent: DurableAgent) -> None:
        from ravi.agents.runtime.durable.context import DurableContext

        run_id = lease.run_id
        ctx = DurableContext(
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
            logger.exception("Agent %s run %s failed", agent.id, run_id)
            for msg in inbox_msgs:
                await self._inbox.nack(agent.id, msg.id, error=str(exc))
            final_seq = await self._event_log.last_seq(run_id)
            await self._event_log.append(
                run_id,
                RunLogEntry(
                    run_id=run_id,
                    seq=final_seq + 1,
                    kind="run.failed",
                    payload={"error": str(exc)},
                ),
                expected_seq=final_seq,
            )
            await self._scheduler.release(lease, status=RunStatus.FAILED)
