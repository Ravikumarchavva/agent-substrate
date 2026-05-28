"""Redis-backed distributed fair-share resource scheduler — Section 7.

Implements :class:`~ravi.platform.scheduling._contracts.SchedulerContract` using Redis
for cross-worker orchestration, atomic state transitions via Lua scripting,
and real-time notifications via Redis Pub/Sub.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from redis.asyncio import Redis

from ravi.platform.scheduling._contracts import (
    PreemptionReason,
    PreemptionSignal,
    ResourceClaim,
    SchedulerCapacity,
    SchedulerContract,
    SlotGrant,
    SlotGrantStatus,
)

logger = logging.getLogger(__name__)

__all__ = ["RedisScheduler"]

UTC = timezone.utc


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


class RedisScheduler(SchedulerContract):
    """Distributed Redis-backed fair-share scheduler.

    Uses Redis hashes and sorted sets to manage active slots and queues,
    atomically checking capacities using Lua scripts, and using Pub/Sub
    to handle distributed slot waiting without polling.
    """

    def __init__(
        self,
        redis_client: Redis[Any],
        *,
        prefix: str = "scheduler",
        max_slots: int = 64,
        max_gpu_slots: int = 4,
        allow_preemption: bool = False,
    ) -> None:
        self._redis = redis_client
        self._prefix = prefix
        self._max_slots = max_slots
        self._max_gpu_slots = max_gpu_slots
        self._allow_preemption = allow_preemption

        # Redis key names
        self._k_active = f"{prefix}:active"
        self._k_queue = f"{prefix}:queue"
        self._k_preemptions = f"{prefix}:preemptions"
        self._k_weights = f"{prefix}:weights"
        self._k_events = f"{prefix}:events"

        # Lua script to request a slot atomically
        self._lua_request = self._redis.register_script("""
            local k_active = KEYS[1]
            local k_queue = KEYS[2]
            local k_preemptions = KEYS[3]
            local k_weights = KEYS[4]
            local k_events = KEYS[5]

            local grant_id = ARGV[1]
            local fqn = ARGV[2]
            local max_slots = tonumber(ARGV[3])
            local max_gpu_slots = tonumber(ARGV[4])
            local gpu_required = ARGV[5] == "true"
            local priority = tonumber(ARGV[6])
            local token_budget = ARGV[7]
            local step_budget = ARGV[8]
            local weight = tonumber(ARGV[9])
            local now_iso = ARGV[10]

            -- Check active count
            local active_keys = redis.call('HKEYS', k_active)
            local active_count = #active_keys

            -- Count active GPU slots
            local active_gpu_count = 0
            for _, k in ipairs(active_keys) do
                local serialized = redis.call('HGET', k_active, k)
                if serialized then
                    local data = cjson.decode(serialized)
                    if data.gpu_required == true then
                        active_gpu_count = active_gpu_count + 1
                    end
                end
            end

            -- Can we fit this claim immediately?
            if active_count < max_slots and (not gpu_required or active_gpu_count < max_gpu_slots) then
                local record = {
                    grant_id = grant_id,
                    principal_fqn = fqn,
                    weight = weight,
                    priority = priority,
                    gpu_required = gpu_required,
                    granted_at = now_iso
                }
                redis.call('HSET', k_active, grant_id, cjson.encode(record))
                return "GRANTED"
            end

            -- If pool full and preemption enabled, find a victim
            if ARGV[11] == "true" and active_count > 0 then
                local best_victim = nil
                local best_victim_priority = priority
                local best_victim_dominance = 9999999.0

                -- Calculate activations per principal for dominance scoring
                local principal_counts = {}
                for _, k in ipairs(active_keys) do
                    local serialized = redis.call('HGET', k_active, k)
                    if serialized then
                        local data = cjson.decode(serialized)
                        local pfqn = data.principal_fqn
                        principal_counts[pfqn] = (principal_counts[pfqn] or 0) + 1
                    end
                end

                for _, k in ipairs(active_keys) do
                    local serialized = redis.call('HGET', k_active, k)
                    if serialized then
                        local data = cjson.decode(serialized)
                        if data.priority < priority then
                            local pfqn = data.principal_fqn
                            local count = principal_counts[pfqn] or 1
                            local dominance = data.weight / count
                            if data.priority < best_victim_priority or (data.priority == best_victim_priority and dominance < best_victim_dominance) then
                                best_victim = data
                                best_victim_priority = data.priority
                                best_victim_dominance = dominance
                            end
                        end
                    end
                end

                if best_victim then
                    local preemption_signal = {
                        grant_id = best_victim.grant_id,
                        reason = "HIGHER_PRIORITY_ARRIVAL",
                        issued_at = now_iso,
                        message = "preempted by " .. fqn .. " (priority=" .. priority .. ")"
                    }
                    redis.call('HDEL', k_active, best_victim.grant_id)
                    redis.call('HSET', k_preemptions, best_victim.grant_id, cjson.encode(preemption_signal))
                    
                    local record = {
                        grant_id = grant_id,
                        principal_fqn = fqn,
                        weight = weight,
                        priority = priority,
                        gpu_required = gpu_required,
                        granted_at = now_iso
                    }
                    redis.call('HSET', k_active, grant_id, cjson.encode(record))
                    redis.call('PUBLISH', k_events, "preempted:" .. best_victim.grant_id)
                    return "GRANTED"
                end
            end

            -- Queue the claim: score = priority * 1e12 + (1e12 - time)
            local time_score = 1e12 - math.floor(redis.call('TIME')[1])
            local score = priority * 1e12 + time_score
            redis.call('ZADD', k_queue, score, grant_id)
            return "QUEUED"
        """)

        # Lua script to release a slot and promote queued claims
        self._lua_release = self._redis.register_script("""
            local k_active = KEYS[1]
            local k_queue = KEYS[2]
            local k_preemptions = KEYS[3]
            local k_weights = KEYS[4]
            local k_events = KEYS[5]

            local grant_id = ARGV[1]
            local max_slots = tonumber(ARGV[2])
            local max_gpu_slots = tonumber(ARGV[3])
            local now_iso = ARGV[4]

            redis.call('HDEL', k_active, grant_id)
            redis.call('HDEL', k_preemptions, grant_id)

            -- Promote queued claims
            local queued = redis.call('ZREVRANGE', k_queue, 0, -1)
            for _, queued_grant_id in ipairs(queued) do
                local active_keys = redis.call('HKEYS', k_active)
                local active_count = #active_keys

                -- Count active GPU slots
                local active_gpu_count = 0
                for _, k in ipairs(active_keys) do
                    local serialized = redis.call('HGET', k_active, k)
                    if serialized then
                        local data = cjson.decode(serialized)
                        if data.gpu_required == true then
                            active_gpu_count = active_gpu_count + 1
                        end
                    end
                end

                if active_count < max_slots then
                    -- Get the principal's FQN and metadata or weight from weights hash
                    -- For RedisScheduler, we can promote and publish
                    redis.call('ZREM', k_queue, queued_grant_id)
                    local record = {
                        grant_id = queued_grant_id,
                        principal_fqn = "promoted",
                        weight = 1.0,
                        priority = 0,
                        gpu_required = false,
                        granted_at = now_iso
                    }
                    redis.call('HSET', k_active, queued_grant_id, cjson.encode(record))
                    redis.call('PUBLISH', k_events, "promoted:" .. queued_grant_id)
                end
            end
            return true
        """)

    async def request_slot(self, claim: ResourceClaim) -> SlotGrant:
        if claim.share_weight <= 0:
            raise ValueError(f"share_weight must be > 0, got {claim.share_weight!r}")

        grant_id = uuid.uuid4().hex
        weight = float(await self._redis.hget(self._k_weights, claim.principal_fqn) or claim.share_weight)

        status_str = await self._lua_request(
            keys=[self._k_active, self._k_queue, self._k_preemptions, self._k_weights, self._k_events],
            args=[
                grant_id,
                claim.principal_fqn,
                str(self._max_slots),
                str(self._max_gpu_slots),
                "true" if claim.gpu_required else "false",
                str(claim.priority),
                str(claim.token_budget),
                str(claim.step_budget),
                str(weight),
                _iso_now(),
                "true" if self._allow_preemption else "false",
            ]
        )

        status = SlotGrantStatus[status_str]

        # Calculate queue position if queued
        queue_pos = None
        if status == SlotGrantStatus.QUEUED:
            queue_pos = await self._redis.zrevrank(self._k_queue, grant_id)

        return SlotGrant(
            grant_id=grant_id,
            principal_fqn=claim.principal_fqn,
            status=status,
            granted_at=_iso_now(),
            granted_tokens=claim.token_budget,
            granted_steps=claim.step_budget,
            queue_position=queue_pos,
        )

    async def release_slot(self, grant_id: str) -> None:
        await self._lua_release(
            keys=[self._k_active, self._k_queue, self._k_preemptions, self._k_weights, self._k_events],
            args=[
                grant_id,
                str(self._max_slots),
                str(self._max_gpu_slots),
                _iso_now(),
            ]
        )

    async def wait_for_slot(self, grant_id: str) -> None:
        # Check if already active
        is_active = await self._redis.hexists(self._k_active, grant_id)
        if is_active:
            return

        # Subscribe to events channel
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(self._k_events)

        try:
            while True:
                # Double-check active state to prevent races
                if await self._redis.hexists(self._k_active, grant_id):
                    break

                # Read next event message
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and isinstance(message.get("data"), bytes):
                    data = message["data"].decode("utf-8")
                    if data == f"promoted:{grant_id}":
                        break
        finally:
            await pubsub.unsubscribe(self._k_events)

    async def check_preemption(self, grant_id: str) -> PreemptionSignal | None:
        raw = await self._redis.hget(self._k_preemptions, grant_id)
        if not raw:
            return None
        data = json.loads(raw)
        return PreemptionSignal(
            grant_id=data["grant_id"],
            reason=PreemptionReason[data["reason"]],
            issued_at=data["issued_at"],
            message=data.get("message", ""),
        )

    async def capacity(self) -> SchedulerCapacity:
        active = await self._redis.hlen(self._k_active)
        queued = await self._redis.zcard(self._k_queue)
        utilization = active / self._max_slots if self._max_slots else 0.0
        return SchedulerCapacity(
            total_slots=self._max_slots,
            active_slots=active,
            queued_claims=queued,
            utilization=utilization,
        )

    async def set_share_weight(self, principal_fqn: str, weight: float) -> None:
        if weight <= 0:
            raise ValueError(f"weight must be > 0, got {weight!r}")
        await self._redis.hset(self._k_weights, principal_fqn, str(weight))
