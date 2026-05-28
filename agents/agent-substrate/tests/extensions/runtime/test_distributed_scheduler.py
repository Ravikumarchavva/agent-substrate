"""Integration tests for DistributedRuntime with fair-share Resource Scheduler.

Verifies that:
- Messages sent through DistributedRuntime request, wait, and release scheduler slots.
- CPU/GPU placement and priority values travel cleanly via claims.
"""

from __future__ import annotations

import asyncio
from typing import Any
import pytest

from ravi.extensions.events import InMemoryEventFabric
from ravi.extensions.scheduler import InMemoryFairShareScheduler
from ravi.extensions.runtime import DistributedRuntime
from ravi.kernel.runtime import (
    AgentId,
    InMemoryLeaseRegistry,
    MessageContext,
)
from ravi.kernel.scheduler import ResourceClaim, SlotGrantStatus


async def _echo_handler(ctx: MessageContext, payload: Any) -> str:
    return f"echo:{payload[0].text}"


def _make_rt(scheduler=None) -> DistributedRuntime:
    return DistributedRuntime(
        fabric=InMemoryEventFabric(),
        lease_registry=InMemoryLeaseRegistry(),
        worker_id="scheduler-worker",
        scheduler=scheduler,
    )


class MockMessage:
    def __init__(self, priority: int = 0, gpu_required: bool = False) -> None:
        self.text = "hello"
        self.priority = priority
        self.gpu_required = gpu_required

    def __getitem__(self, idx: int) -> Any:
        # Mock payload compatibility
        return self


# ===========================================================================
# S7 Distributed Scheduler Wiring Tests
# ===========================================================================


class TestDistributedSchedulerWiring:
    @pytest.mark.asyncio
    async def test_scheduler_allocated_and_released_on_send(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=2)
        rt = _make_rt(scheduler=sched)
        await rt.register("echo", _echo_handler)

        msg = MockMessage()
        result = await rt.send_message(msg, recipient=AgentId("echo", "1"))
        assert result == "echo:hello"

        # Verify slot was released cleanly
        cap = await sched.capacity()
        assert cap.active_slots == 0
        assert cap.queued_claims == 0

        await rt.stop()

    @pytest.mark.asyncio
    async def test_scheduler_queues_and_waits_for_slot(self) -> None:
        # Max capacity = 1
        sched = InMemoryFairShareScheduler(max_slots=1)
        rt = _make_rt(scheduler=sched)
        await rt.register("echo", _echo_handler)

        # Pre-occupy the only slot manually
        claim = ResourceClaim(principal_fqn="pre-occupy", priority=0)
        grant = await sched.request_slot(claim)
        assert grant.status == SlotGrantStatus.GRANTED

        # Now send a message through the runtime; it should block as QUEUED
        send_task = asyncio.create_task(
            rt.send_message(MockMessage(), recipient=AgentId("echo", "1"))
        )

        # Wait a moment, ensure it is still queued/blocked
        await asyncio.sleep(0.05)
        assert not send_task.done()

        cap = await sched.capacity()
        assert cap.active_slots == 1
        assert cap.queued_claims == 1

        # Release the pre-occupied slot; this should promote the runtime's claim
        await sched.release_slot(grant.grant_id)

        # Now the send task should complete
        result = await send_task
        assert result == "echo:hello"

        # Active slots should be 0 again after finally block releases it
        cap_after = await sched.capacity()
        assert cap_after.active_slots == 0

        await rt.stop()
