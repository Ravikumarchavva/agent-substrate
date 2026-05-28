"""Tests for RedisScheduler.

All Redis interactions are mocked so tests run instantly without needing
a live Redis database.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravi.integrations.scheduler import RedisScheduler
from ravi.platform.scheduling import (
    PreemptionReason,
    ResourceClaim,
    SchedulerContract,
    SlotGrantStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _claim(
    fqn: str = "human/tenant-1/ws-1/alice",
    *,
    token_budget: int = 100,
    step_budget: int = 10,
    share_weight: float = 1.0,
    priority: int = 0,
    gpu_required: bool = False,
) -> ResourceClaim:
    return ResourceClaim(
        principal_fqn=fqn,
        token_budget=token_budget,
        step_budget=step_budget,
        share_weight=share_weight,
        priority=priority,
        gpu_required=gpu_required,
    )


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestProtocolConformance:
    def test_satisfies_protocol(self) -> None:
        mock_redis = MagicMock()
        sched = RedisScheduler(mock_redis)
        assert isinstance(sched, SchedulerContract)


# ===========================================================================
# request_slot
# ===========================================================================


class TestRequestSlot:
    @pytest.mark.asyncio
    async def test_request_slot_granted_immediately(self) -> None:
        mock_redis = AsyncMock()
        # Mock Lua script eval return
        mock_script = AsyncMock(return_value="GRANTED")
        mock_redis.register_script = MagicMock(return_value=mock_script)

        sched = RedisScheduler(mock_redis)
        claim = _claim()

        grant = await sched.request_slot(claim)

        assert grant.status == SlotGrantStatus.GRANTED
        assert grant.grant_id
        assert grant.principal_fqn == claim.principal_fqn

    @pytest.mark.asyncio
    async def test_request_slot_queued(self) -> None:
        mock_redis = AsyncMock()
        mock_script = AsyncMock(return_value="QUEUED")
        mock_redis.register_script = MagicMock(return_value=mock_script)
        # Mock zrevrank
        mock_redis.zrevrank = AsyncMock(return_value=3)

        sched = RedisScheduler(mock_redis)
        claim = _claim()

        grant = await sched.request_slot(claim)

        assert grant.status == SlotGrantStatus.QUEUED
        assert grant.queue_position == 3

    @pytest.mark.asyncio
    async def test_invalid_share_weight_raises(self) -> None:
        mock_redis = AsyncMock()
        sched = RedisScheduler(mock_redis)
        with pytest.raises(ValueError, match="share_weight"):
            await sched.request_slot(_claim(share_weight=-0.5))


# ===========================================================================
# release_slot
# ===========================================================================


class TestReleaseSlot:
    @pytest.mark.asyncio
    async def test_release_slot_calls_lua(self) -> None:
        mock_redis = AsyncMock()
        mock_script = AsyncMock(return_value=True)
        mock_redis.register_script = MagicMock(return_value=mock_script)

        sched = RedisScheduler(mock_redis)
        await sched.release_slot("some-grant-id")

        assert mock_script.called


# ===========================================================================
# wait_for_slot
# ===========================================================================


class TestWaitForSlot:
    @pytest.mark.asyncio
    async def test_wait_for_slot_returns_immediately_if_active(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.hexists = AsyncMock(return_value=True)

        sched = RedisScheduler(mock_redis)
        # Should return immediately without subscribing
        await sched.wait_for_slot("grant-123")
        assert not mock_redis.pubsub.called

    @pytest.mark.asyncio
    async def test_wait_for_slot_subscribes_and_awaits(self) -> None:
        mock_redis = AsyncMock()
        # First call hexists = False, second call hexists = True (promoted)
        hexists_calls = 0

        async def hexists_mock(key: str, field: str) -> bool:
            nonlocal hexists_calls
            hexists_calls += 1
            return hexists_calls > 1

        mock_redis.hexists = hexists_mock

        mock_pubsub = AsyncMock()
        mock_redis.pubsub = MagicMock(return_value=mock_pubsub)

        sched = RedisScheduler(mock_redis)
        await sched.wait_for_slot("grant-abc")

        mock_pubsub.subscribe.assert_called_once_with(sched._k_events)
        mock_pubsub.unsubscribe.assert_called_once_with(sched._k_events)


# ===========================================================================
# check_preemption
# ===========================================================================


class TestCheckPreemption:
    @pytest.mark.asyncio
    async def test_check_preemption_returns_none_if_no_signal(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.hget = AsyncMock(return_value=None)

        sched = RedisScheduler(mock_redis)
        sig = await sched.check_preemption("grant-id")
        assert sig is None

    @pytest.mark.asyncio
    async def test_check_preemption_parses_json(self) -> None:
        mock_redis = AsyncMock()
        signal_data = {
            "grant_id": "grant-abc",
            "reason": "HIGHER_PRIORITY_ARRIVAL",
            "issued_at": "2026-01-01T00:00:00Z",
            "message": "preempted",
        }
        mock_redis.hget = AsyncMock(return_value=json.dumps(signal_data))

        sched = RedisScheduler(mock_redis)
        sig = await sched.check_preemption("grant-abc")

        assert sig is not None
        assert sig.grant_id == "grant-abc"
        assert sig.reason == PreemptionReason.HIGHER_PRIORITY_ARRIVAL
        assert sig.message == "preempted"


# ===========================================================================
# capacity & weights
# ===========================================================================


class TestCapacityAndWeights:
    @pytest.mark.asyncio
    async def test_capacity(self) -> None:
        mock_redis = AsyncMock()
        mock_redis.hlen = AsyncMock(return_value=12)
        mock_redis.zcard = AsyncMock(return_value=3)

        sched = RedisScheduler(mock_redis, max_slots=20)
        cap = await sched.capacity()

        assert cap.total_slots == 20
        assert cap.active_slots == 12
        assert cap.queued_claims == 3
        assert cap.utilization == pytest.approx(0.6)

    @pytest.mark.asyncio
    async def test_set_share_weight(self) -> None:
        mock_redis = AsyncMock()
        sched = RedisScheduler(mock_redis)

        await sched.set_share_weight("principal-fqn", 2.5)

        mock_redis.hset.assert_called_once_with(sched._k_weights, "principal-fqn", "2.5")

    @pytest.mark.asyncio
    async def test_set_share_weight_invalid_raises(self) -> None:
        mock_redis = AsyncMock()
        sched = RedisScheduler(mock_redis)
        with pytest.raises(ValueError, match="weight"):
            await sched.set_share_weight("fqn", -1.0)
