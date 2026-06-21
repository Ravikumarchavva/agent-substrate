"""InMemoryScheduler — Stage 0 single-process asyncio work-queue.

Coalescing guarantee: enqueuing a run_id that is already pending is a no-op
(the Wakeup is merged into the existing entry).  Workers receive each run at
most once per wake-cycle regardless of how many sources enqueued it.

In Stage 0 runs are in-process asyncio Tasks, so the Scheduler primarily
acts as a ready-queue and state-tracker.  Stage 1 replaces this with
Postgres SELECT … FOR UPDATE SKIP LOCKED.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import AsyncIterator

from agent_substrate.kernel.core.identity import AgentId
from agent_substrate.kernel.runtime.ids import RunId, RunStatus
from agent_substrate.kernel.runtime.scheduler import Lease, RunRetryPolicy
from agent_substrate.kernel.runtime.wakeup import Wakeup


class InMemoryScheduler:
    """Single-process scheduler backed by an asyncio.PriorityQueue.

    Queue entries: ``(priority, monotonic_tie, run_id)`` — lower priority
    value = higher scheduling precedence (min-heap).
    """

    def __init__(self) -> None:
        # (priority, tie_break, run_id)
        self._queue: asyncio.PriorityQueue[tuple[int, float, RunId]] = (
            asyncio.PriorityQueue()
        )
        self._pending: set[RunId] = set()  # coalescing: don't enqueue twice
        self._leases: dict[RunId, Lease] = {}
        self._status: dict[RunId, RunStatus] = {}
        self._agents: dict[RunId, AgentId] = {}  # run_id → which agent to wake
        self._wakeups: dict[RunId, Wakeup | None] = {}
        self._retry_policies: dict[RunId, RunRetryPolicy] = {}
        self._retry_counts: dict[RunId, int] = {}

    def register_run(self, run_id: RunId, agent_id: AgentId) -> None:
        """Associate a run_id with its agent before enqueue."""
        self._agents[run_id] = agent_id

    def agent_for(self, run_id: RunId) -> AgentId | None:
        return self._agents.get(run_id)

    def wakeup_for(self, run_id: RunId) -> Wakeup | None:
        return self._wakeups.get(run_id)

    async def enqueue(
        self,
        run_id: RunId,
        *,
        priority: int,
        tenant: str,
        wake: Wakeup | None = None,
        retry_policy: RunRetryPolicy | None = None,
    ) -> None:
        if run_id in self._pending or run_id in self._leases:
            # Coalesce: merge wakeup but don't add duplicate entry
            if wake:
                self._wakeups[run_id] = wake
            return
        self._pending.add(run_id)
        self._status[run_id] = RunStatus.PENDING
        self._wakeups[run_id] = wake
        if retry_policy:
            self._retry_policies[run_id] = retry_policy
        await self._queue.put((priority, time.monotonic(), run_id))

    async def lease(self, *, worker_id: str, capacity: int) -> list[Lease]:
        leases: list[Lease] = []
        while len(leases) < capacity:
            try:
                _, _, run_id = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._pending.discard(run_id)
            if self._status.get(run_id) == RunStatus.CANCELLED:
                continue
            expires_at = datetime.now(tz=timezone.utc) + timedelta(seconds=30)
            attempt = self._retry_counts.get(run_id, 0)
            agent_id = self._agents.get(run_id)
            if agent_id is None:
                # Run was registered without a known agent — skip it
                self._status[run_id] = RunStatus.FAILED
                continue
            lease = Lease(
                run_id=run_id,
                agent_id=agent_id,
                worker_id=worker_id,
                expires_at=expires_at,
                attempt=attempt,
            )
            self._leases[run_id] = lease
            self._status[run_id] = RunStatus.RUNNING
            leases.append(lease)
        return leases

    async def heartbeat(self, lease: Lease) -> None:
        # In-process: no-op; lease expiry is not enforced within a single process
        pass

    async def release(
        self,
        lease: Lease,
        *,
        status: RunStatus,
        wake_on: Wakeup | None = None,
    ) -> None:
        self._leases.pop(lease.run_id, None)
        self._status[lease.run_id] = status

        if status == RunStatus.FAILED:
            policy = self._retry_policies.get(lease.run_id, RunRetryPolicy())
            count = self._retry_counts.get(lease.run_id, 0)
            if count < policy.max_retries:
                self._retry_counts[lease.run_id] = count + 1
                await self.enqueue(
                    lease.run_id, priority=5, tenant="default", wake=wake_on
                )
                return

        if status == RunStatus.SUSPENDED and wake_on:
            self._wakeups[lease.run_id] = wake_on
            # For timer wakeups the SignalBus will call enqueue when it fires.
            # For signal wakeups same.  For message wakeups the Inbox on_deliver
            # hook calls enqueue.  Do NOT enqueue here — that would defeat dormancy.

    async def pending_runs(self, *, tenant: str | None = None) -> AsyncIterator[RunId]:
        return self._pending_iter()

    async def _pending_iter(self) -> AsyncIterator[RunId]:  # type: ignore[return]
        for run_id in list(self._pending):
            yield run_id

    async def get_status(self, run_id: RunId) -> RunStatus | None:
        return self._status.get(run_id)

    async def wake_suspended(self, run_id: RunId, *, priority: int = 5) -> None:
        """Re-enqueue a suspended run (called by SignalBus/Inbox when a wakeup fires)."""
        if self._status.get(run_id) == RunStatus.SUSPENDED:
            await self.enqueue(run_id, priority=priority, tenant="default")

    async def find_run_for_agent(
        self, agent_id: AgentId
    ) -> tuple[RunId, RunStatus] | None:
        """Return (run_id, status) of the most recent non-terminal run for agent_id.

        Returns None when no active run exists (all runs are terminal or
        this agent has never had a run).
        """
        _terminal = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
        for run_id, aid in list(self._agents.items()):
            if aid == agent_id:
                status = self._status.get(run_id)
                if status is not None and status not in _terminal:
                    return (run_id, status)
        return None

    async def wake_agent(self, agent_id: AgentId, *, priority: int = 5) -> None:
        """Wake the active run for agent_id, if any."""
        for run_id, aid in list(self._agents.items()):
            if aid == agent_id:
                await self.wake_suspended(run_id, priority=priority)
                break
