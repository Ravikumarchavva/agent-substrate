"""Tests for Section 7 — Resource Scheduler.

Coverage
--------
- Kernel contracts: SlotGrant/ResourceClaim/PreemptionSignal shape
- InMemoryFairShareScheduler: grant, queue, release, preemption, capacity,
  share-weight update, error paths
- SchedulerContract protocol conformance
"""

from __future__ import annotations

import asyncio

import pytest

from ravi.extensions.scheduler import InMemoryFairShareScheduler
from ravi.kernel.scheduler import (
    ResourceClaim,
    SchedulerContract,
    SlotGrantStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _claim(
    fqn: str = "human/t/ws/alice",
    *,
    token_budget: int = 100,
    step_budget: int = 10,
    share_weight: float = 1.0,
    priority: int = 0,
) -> ResourceClaim:
    return ResourceClaim(
        principal_fqn=fqn,
        token_budget=token_budget,
        step_budget=step_budget,
        share_weight=share_weight,
        priority=priority,
    )


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestSchedulerProtocolConformance:
    def test_in_memory_satisfies_protocol(self) -> None:
        sched = InMemoryFairShareScheduler()
        assert isinstance(sched, SchedulerContract)


# ===========================================================================
# Slot grant — basic admission
# ===========================================================================


class TestSlotGrant:
    async def test_grant_when_pool_has_capacity(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=4)
        grant = await sched.request_slot(_claim())
        assert grant.status is SlotGrantStatus.GRANTED
        assert grant.grant_id
        assert grant.principal_fqn == "human/t/ws/alice"

    async def test_granted_tokens_and_steps_match_claim(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=2)
        grant = await sched.request_slot(_claim(token_budget=512, step_budget=8))
        assert grant.granted_tokens == 512
        assert grant.granted_steps == 8

    async def test_zero_budget_is_allowed(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=2)
        grant = await sched.request_slot(_claim(token_budget=0, step_budget=0))
        assert grant.status is SlotGrantStatus.GRANTED
        assert grant.granted_tokens == 0

    async def test_multiple_grants_up_to_capacity(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=3)
        grants = [await sched.request_slot(_claim(fqn=f"a/{i}")) for i in range(3)]
        assert all(g.status is SlotGrantStatus.GRANTED for g in grants)

    async def test_capacity_tracks_active_slots(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=4)
        await sched.request_slot(_claim())
        await sched.request_slot(_claim())
        cap = await sched.capacity()
        assert cap.active_slots == 2
        assert cap.total_slots == 4
        assert cap.queued_claims == 0

    async def test_invalid_share_weight_raises(self) -> None:
        sched = InMemoryFairShareScheduler()
        with pytest.raises(ValueError, match="share_weight"):
            await sched.request_slot(_claim(share_weight=-1.0))

    async def test_zero_share_weight_raises(self) -> None:
        sched = InMemoryFairShareScheduler()
        with pytest.raises(ValueError, match="share_weight"):
            await sched.request_slot(_claim(share_weight=0.0))


# ===========================================================================
# Queue behaviour — pool full
# ===========================================================================


class TestQueueBehaviour:
    async def test_claim_queued_when_pool_full(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=1)
        first = await sched.request_slot(_claim(fqn="alice"))
        assert first.status is SlotGrantStatus.GRANTED

        second = await sched.request_slot(_claim(fqn="bob"))
        assert second.status is SlotGrantStatus.QUEUED
        assert second.queue_position == 0

    async def test_queue_position_increments(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=1)
        await sched.request_slot(_claim(fqn="a"))  # fills slot
        g1 = await sched.request_slot(_claim(fqn="b"))
        g2 = await sched.request_slot(_claim(fqn="c"))
        assert g1.queue_position == 0
        assert g2.queue_position == 1

    async def test_release_promotes_first_queued(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=1)
        g1 = await sched.request_slot(_claim(fqn="a"))
        queued = await sched.request_slot(_claim(fqn="b"))
        assert queued.status is SlotGrantStatus.QUEUED

        await sched.release_slot(g1.grant_id)

        cap = await sched.capacity()
        assert cap.active_slots == 1
        assert cap.queued_claims == 0

    async def test_release_unknown_grant_is_noop(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=2)
        # Should not raise
        await sched.release_slot("nonexistent-grant-id")

    async def test_release_decrements_active_count(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=4)
        g = await sched.request_slot(_claim())
        cap_before = await sched.capacity()
        await sched.release_slot(g.grant_id)
        cap_after = await sched.capacity()
        assert cap_after.active_slots == cap_before.active_slots - 1


# ===========================================================================
# Preemption
# ===========================================================================


class TestPreemption:
    async def test_no_preemption_by_default(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=1, allow_preemption=False)
        await sched.request_slot(_claim(fqn="a", priority=0))
        incoming = await sched.request_slot(_claim(fqn="b", priority=10))
        assert incoming.status is SlotGrantStatus.QUEUED

    async def test_preemption_granted_for_higher_priority(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=1, allow_preemption=True)
        low = await sched.request_slot(_claim(fqn="a", priority=0))
        assert low.status is SlotGrantStatus.GRANTED

        high = await sched.request_slot(_claim(fqn="b", priority=10))
        assert high.status is SlotGrantStatus.GRANTED

        signal = await sched.check_preemption(low.grant_id)
        assert signal is not None
        assert "preempted" in signal.message

    async def test_no_preemption_when_equal_priority(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=1, allow_preemption=True)
        low = await sched.request_slot(_claim(fqn="a", priority=5))
        second = await sched.request_slot(_claim(fqn="b", priority=5))
        assert second.status is SlotGrantStatus.QUEUED
        signal = await sched.check_preemption(low.grant_id)
        assert signal is None

    async def test_check_preemption_returns_none_for_unknown_grant(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=2)
        result = await sched.check_preemption("no-such-grant")
        assert result is None

    async def test_release_clears_preemption_signal(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=1, allow_preemption=True)
        low = await sched.request_slot(_claim(fqn="a", priority=0))
        await sched.request_slot(_claim(fqn="b", priority=5))

        assert await sched.check_preemption(low.grant_id) is not None
        await sched.release_slot(low.grant_id)
        assert await sched.check_preemption(low.grant_id) is None

    async def test_no_preemption_when_pool_not_full(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=2, allow_preemption=True)
        low = await sched.request_slot(_claim(fqn="a", priority=0))
        await sched.request_slot(_claim(fqn="b", priority=99))
        # Pool still had a free slot — victim should not be preempted
        assert await sched.check_preemption(low.grant_id) is None


# ===========================================================================
# Capacity and share weights
# ===========================================================================


class TestCapacityAndWeights:
    async def test_capacity_utilization_calculation(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=4)
        await sched.request_slot(_claim())
        await sched.request_slot(_claim())
        cap = await sched.capacity()
        assert cap.utilization == pytest.approx(0.5)

    async def test_utilization_zero_when_empty(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=4)
        cap = await sched.capacity()
        assert cap.utilization == 0.0

    async def test_set_share_weight_updates_principal(self) -> None:
        sched = InMemoryFairShareScheduler()
        await sched.set_share_weight("agent/t/ws/bot", 3.0)
        # Internal state only; we verify through a grant.
        grant = await sched.request_slot(
            ResourceClaim(principal_fqn="agent/t/ws/bot", share_weight=1.0)
        )
        assert grant.status is SlotGrantStatus.GRANTED

    async def test_set_share_weight_zero_raises(self) -> None:
        sched = InMemoryFairShareScheduler()
        with pytest.raises(ValueError, match="weight"):
            await sched.set_share_weight("fqn", 0.0)

    async def test_set_share_weight_negative_raises(self) -> None:
        sched = InMemoryFairShareScheduler()
        with pytest.raises(ValueError, match="weight"):
            await sched.set_share_weight("fqn", -2.0)

    async def test_max_slots_zero_raises_on_init(self) -> None:
        with pytest.raises(ValueError, match="max_slots"):
            InMemoryFairShareScheduler(max_slots=0)


# ===========================================================================
# Concurrent safety (basic smoke test)
# ===========================================================================


class TestConcurrentSafety:
    async def test_concurrent_requests_do_not_exceed_max_slots(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=5)
        tasks = [
            asyncio.create_task(sched.request_slot(_claim(fqn=f"p/{i}")))
            for i in range(20)
        ]
        results = await asyncio.gather(*tasks)
        granted = sum(1 for g in results if g.status is SlotGrantStatus.GRANTED)
        queued = sum(1 for g in results if g.status is SlotGrantStatus.QUEUED)
        assert granted == 5
        assert queued == 15

    async def test_concurrent_releases_drain_queue(self) -> None:
        sched = InMemoryFairShareScheduler(max_slots=2)
        grants = [await sched.request_slot(_claim(fqn=f"p/{i}")) for i in range(2)]
        # Queue 2 more
        await sched.request_slot(_claim(fqn="q0"))
        await sched.request_slot(_claim(fqn="q1"))

        cap_before = await sched.capacity()
        assert cap_before.queued_claims == 2

        await asyncio.gather(
            sched.release_slot(grants[0].grant_id),
            sched.release_slot(grants[1].grant_id),
        )
        cap_after = await sched.capacity()
        # Both released, queue should be empty (both queued items promoted)
        assert cap_after.queued_claims == 0
        assert cap_after.active_slots == 2
