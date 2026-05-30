"""Redis backed LeaseRegistry.

Implements :class:`ravi.kernel.runtime._lease.LeaseRegistry` using Redis TTL
keys and Lua scripts for atomic compare-and-set semantics.

Each logical agent's lease is stored at ``{key_prefix}{agent_id}`` as a JSON
blob with a TTL equal to the requested lease duration.

Atomicity
---------
All mutating operations (acquire, renew, release) use inline Lua scripts so
that the read-check-write sequence is executed atomically inside Redis.

Thread-safety
-------------
``_lock`` guards ``_client`` initialisation.  No lock is held across an
``await``.
"""

from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import redis.asyncio as aioredis

from ravi.kernel.runtime._identity import AgentId
from ravi.kernel.runtime._lease import (
    DEFAULT_LEASE_TTL_SECONDS,
    LeaseAcquisitionResult,
)
from ravi.kernel.runtime._lifecycle import ExecutionLease

__all__ = ["RedisLeaseRegistry"]

logger = logging.getLogger(__name__)
UTC = timezone.utc

# ---------------------------------------------------------------------------
# Lua scripts — executed atomically inside Redis
# ---------------------------------------------------------------------------

# Acquire: SET NX PX only if key is absent.
# Returns: [1, json_stored]  on success
#          [0, json_existing] if already taken
_ACQUIRE_SCRIPT = """
local existing = redis.call('GET', KEYS[1])
if existing then
    return {0, existing}
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', tonumber(ARGV[2]))
return {1, ARGV[1]}
"""

# Renew: extend TTL only if the caller is still the holder (matched by worker_id).
# Returns: [1, json_updated]  on success
#          [0, ""]            if key expired
#          [0, json_existing] if different holder
_RENEW_SCRIPT = """
local existing = redis.call('GET', KEYS[1])
if not existing then
    return {0, ""}
end
local ok, data = pcall(cjson.decode, existing)
if not ok then
    return {0, existing}
end
if data['worker_id'] ~= ARGV[1] then
    return {0, existing}
end
redis.call('SET', KEYS[1], ARGV[2], 'PX', tonumber(ARGV[3]))
return {1, ARGV[2]}
"""

# Release: delete only if the caller is still the holder (matched by both
# worker_id and lease_id).
# Returns: 1 — deleted (or was already gone)
#          0 — key exists but belongs to a different holder
_RELEASE_SCRIPT = """
local existing = redis.call('GET', KEYS[1])
if not existing then
    return 1
end
local ok, data = pcall(cjson.decode, existing)
if not ok then
    return 0
end
if data['worker_id'] ~= ARGV[1] or data['lease_id'] ~= ARGV[2] then
    return 0
end
redis.call('DEL', KEYS[1])
return 1
"""


class RedisLeaseRegistry:
    """Redis backed implementation of :class:`LeaseRegistry`."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        *,
        key_prefix: str = "ravi:lease:",
    ) -> None:
        self._url = redis_url
        self._prefix = key_prefix
        self._lock = threading.RLock()
        self._client: aioredis.Redis | None = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _lease_key(self, agent_id: AgentId) -> str:
        return f"{self._prefix}{agent_id}"

    async def _redis(self) -> aioredis.Redis:
        with self._lock:
            if self._client is None:
                self._client = aioredis.from_url(
                    self._url, decode_responses=True
                )
        return self._client

    @staticmethod
    def _make_lease(
        agent_id: AgentId,
        worker_id: str,
        *,
        ttl_seconds: float,
        lease_id: str | None = None,
        granted_at: datetime | None = None,
    ) -> tuple[ExecutionLease, int]:
        """Build an :class:`ExecutionLease` and its TTL in milliseconds."""
        now = datetime.now(UTC)
        granted = granted_at or now
        expires = now + timedelta(seconds=ttl_seconds)
        lid = lease_id or uuid.uuid4().hex
        ttl_ms = max(1, int(ttl_seconds * 1000))
        lease = ExecutionLease(
            agent_id_str=str(agent_id),
            worker_id=worker_id,
            lease_id=lid,
            granted_at=granted.isoformat(),
            expires_at=expires.isoformat(),
        )
        return lease, ttl_ms

    @staticmethod
    def _lease_to_json(lease: ExecutionLease) -> str:
        return json.dumps(
            {
                "agent_id_str": lease.agent_id_str,
                "worker_id": lease.worker_id,
                "lease_id": lease.lease_id,
                "granted_at": lease.granted_at,
                "expires_at": lease.expires_at,
                "budget_tokens": lease.budget_tokens,
                "budget_steps": lease.budget_steps,
            }
        )

    @staticmethod
    def _lease_from_json(raw: str) -> ExecutionLease:
        d: dict[str, Any] = json.loads(raw)
        return ExecutionLease(
            agent_id_str=d["agent_id_str"],
            worker_id=d["worker_id"],
            lease_id=d["lease_id"],
            granted_at=d["granted_at"],
            expires_at=d["expires_at"],
            budget_tokens=d.get("budget_tokens", 0),
            budget_steps=d.get("budget_steps", 0),
        )

    # ------------------------------------------------------------------
    # LeaseRegistry protocol
    # ------------------------------------------------------------------

    async def acquire(
        self,
        agent_id: AgentId,
        worker_id: str,
        *,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> LeaseAcquisitionResult:
        """Atomically acquire the lease; fail if already held by another worker."""
        client = await self._redis()
        key = self._lease_key(agent_id)
        lease, ttl_ms = self._make_lease(agent_id, worker_id, ttl_seconds=ttl_seconds)
        lease_json = self._lease_to_json(lease)

        result = await client.eval(_ACQUIRE_SCRIPT, 1, key, lease_json, str(ttl_ms))

        flag = int(result[0])
        if flag == 1:
            logger.debug("lease.acquire acquired agent=%s worker=%s", agent_id, worker_id)
            return LeaseAcquisitionResult(lease=lease)

        # Someone else holds it — parse their lease for the caller.
        holder_raw: str = result[1]
        try:
            current_holder = self._lease_from_json(holder_raw)
        except Exception:  # noqa: BLE001
            current_holder = None
        logger.debug("lease.acquire contended agent=%s holder=%s", agent_id, current_holder)
        return LeaseAcquisitionResult(lease=None, current_holder=current_holder)

    async def renew(
        self,
        lease: ExecutionLease,
        *,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> ExecutionLease | None:
        """Extend the lease TTL; return the renewed lease or None if lost."""
        client = await self._redis()
        agent_id_str = lease.agent_id_str
        # Reconstruct AgentId to build the key consistently.
        key = f"{self._prefix}{agent_id_str}"
        ttl_ms = max(1, int(ttl_seconds * 1000))

        now = datetime.now(UTC)
        renewed = ExecutionLease(
            agent_id_str=lease.agent_id_str,
            worker_id=lease.worker_id,
            lease_id=lease.lease_id,
            granted_at=lease.granted_at,
            expires_at=(now + timedelta(seconds=ttl_seconds)).isoformat(),
            budget_tokens=lease.budget_tokens,
            budget_steps=lease.budget_steps,
        )
        renewed_json = self._lease_to_json(renewed)

        result = await client.eval(
            _RENEW_SCRIPT, 1, key, lease.worker_id, renewed_json, str(ttl_ms)
        )

        flag = int(result[0])
        if flag == 1:
            logger.debug("lease.renew ok agent=%s", agent_id_str)
            return renewed

        logger.debug("lease.renew lost agent=%s", agent_id_str)
        return None

    async def release(self, lease: ExecutionLease) -> None:
        """Surrender the lease; no-op if already released or stolen."""
        client = await self._redis()
        key = f"{self._prefix}{lease.agent_id_str}"
        await client.eval(_RELEASE_SCRIPT, 1, key, lease.worker_id, lease.lease_id)
        logger.debug("lease.release agent=%s worker=%s", lease.agent_id_str, lease.worker_id)

    async def current(self, agent_id: AgentId) -> ExecutionLease | None:
        """Return the active lease for agent_id, or None if absent/expired."""
        client = await self._redis()
        key = self._lease_key(agent_id)
        raw = await client.get(key)
        if raw is None:
            return None
        try:
            return self._lease_from_json(raw)
        except Exception:  # noqa: BLE001
            logger.warning("lease.current: corrupt JSON for agent=%s", agent_id)
            return None
