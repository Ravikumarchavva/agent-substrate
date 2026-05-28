"""Lease registry — coordinates exclusive activation of logical agents.

A *logical* agent identity (``AgentId``) can be hosted by exactly one worker
at a time. The :class:`LeaseRegistry` Protocol formalises that invariant so
multiple workers — local threads in a free-threaded process, separate
asyncio loops, or full peer processes coordinated through Redis — race-safely
agree on who owns each agent.

The kernel ships an in-process :class:`InMemoryLeaseRegistry` reference
implementation. Real distributed deployments plug in a Redis / etcd /
Postgres backend that implements the same Protocol.

Why the lock?
~~~~~~~~~~~~~
Python 3.14 free-threaded builds drop the GIL, which makes plain
``dict[str, ExecutionLease]`` mutations no longer atomic across threads.
Even on GIL builds, lease registries are accessed by:

* the runtime's agent-activation path (event-loop thread)
* the lease-renewal heartbeat (background thread)
* observer / metric collectors (background thread or thread-pool)

A single ``threading.RLock`` is the smallest correct guard.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from ravi.kernel.runtime._identity import AgentId
from ravi.kernel.runtime._lifecycle import ExecutionLease

__all__ = [
    "LeaseAcquisitionResult",
    "LeaseRegistry",
    "InMemoryLeaseRegistry",
    "DEFAULT_LEASE_TTL_SECONDS",
]


UTC = timezone.utc
DEFAULT_LEASE_TTL_SECONDS: float = 60.0


# ---------------------------------------------------------------------------
# Result wrapper — distinguish "you got it" from "someone else has it"
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LeaseAcquisitionResult:
    """Outcome of a :meth:`LeaseRegistry.acquire` call.

    ``lease`` is non-None on success. ``current_holder`` describes who holds
    the contested lease when acquisition fails — useful for routing the
    caller's envelope to the existing holder rather than failing the request.
    """

    lease: ExecutionLease | None
    current_holder: ExecutionLease | None = None

    @property
    def acquired(self) -> bool:
        return self.lease is not None


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class LeaseRegistry(Protocol):
    """Coordination contract — one worker per logical agent at a time.

    Implementations must serialise concurrent ``acquire``/``renew``/``release``
    calls so the "single holder" invariant cannot be violated under any
    interleaving — including the no-GIL free-threaded interleavings.
    """

    async def acquire(
        self,
        agent_id: AgentId,
        worker_id: str,
        *,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> LeaseAcquisitionResult:
        """Attempt to grant ``worker_id`` exclusive ownership of ``agent_id``.

        Returns :class:`LeaseAcquisitionResult` with ``lease=None`` and
        ``current_holder`` populated when another worker already holds an
        unexpired lease.
        """
        ...

    async def renew(
        self,
        lease: ExecutionLease,
        *,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> ExecutionLease | None:
        """Extend ``lease`` by ``ttl_seconds`` from now.

        Returns the renewed lease, or ``None`` when the lease was lost
        (stolen by another worker, or expired before renewal).
        """
        ...

    async def release(self, lease: ExecutionLease) -> None:
        """Surrender ``lease``. No-op if the caller no longer holds it."""
        ...

    async def current(self, agent_id: AgentId) -> ExecutionLease | None:
        """Return the active lease for ``agent_id``, or ``None``."""
        ...


# ---------------------------------------------------------------------------
# In-memory reference implementation
# ---------------------------------------------------------------------------


@dataclass
class _LeaseRecord:
    """In-memory storage representation — separated from the public ExecutionLease."""

    lease: ExecutionLease
    expires_at_native: datetime = field(default_factory=lambda: datetime.now(UTC))


class InMemoryLeaseRegistry:
    """Single-process :class:`LeaseRegistry` implementation.

    Backed by a ``dict`` guarded by a ``threading.RLock`` so it remains
    correct under Python 3.14 free-threaded execution and from background
    heartbeat threads. The async methods do not yield while holding the lock.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._leases: dict[str, _LeaseRecord] = {}

    # ----- public API ----------------------------------------------------

    async def acquire(
        self,
        agent_id: AgentId,
        worker_id: str,
        *,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> LeaseAcquisitionResult:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be > 0, got {ttl_seconds!r}")

        key = str(agent_id)
        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)

        with self._lock:
            existing = self._leases.get(key)
            if existing is not None and existing.expires_at_native > now:
                return LeaseAcquisitionResult(
                    lease=None, current_holder=existing.lease
                )

            lease = ExecutionLease(
                agent_id_str=key,
                worker_id=worker_id,
                lease_id=uuid.uuid4().hex,
                granted_at=now.isoformat(),
                expires_at=expires_at.isoformat(),
            )
            self._leases[key] = _LeaseRecord(
                lease=lease, expires_at_native=expires_at
            )
            return LeaseAcquisitionResult(lease=lease, current_holder=None)

    async def renew(
        self,
        lease: ExecutionLease,
        *,
        ttl_seconds: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> ExecutionLease | None:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be > 0, got {ttl_seconds!r}")

        now = datetime.now(UTC)
        expires_at = now + timedelta(seconds=ttl_seconds)

        with self._lock:
            current = self._leases.get(lease.agent_id_str)
            # Must be the exact same lease — token mismatch means we lost it.
            if current is None or current.lease.lease_id != lease.lease_id:
                return None
            # Refuse to renew if the lease already expired — the caller
            # must re-acquire instead of silently extending a stale lease.
            if current.expires_at_native <= now:
                del self._leases[lease.agent_id_str]
                return None

            renewed = ExecutionLease(
                agent_id_str=lease.agent_id_str,
                worker_id=lease.worker_id,
                lease_id=lease.lease_id,
                granted_at=lease.granted_at,
                expires_at=expires_at.isoformat(),
                budget_tokens=lease.budget_tokens,
                budget_steps=lease.budget_steps,
            )
            self._leases[lease.agent_id_str] = _LeaseRecord(
                lease=renewed, expires_at_native=expires_at
            )
            return renewed

    async def release(self, lease: ExecutionLease) -> None:
        with self._lock:
            current = self._leases.get(lease.agent_id_str)
            if current is not None and current.lease.lease_id == lease.lease_id:
                del self._leases[lease.agent_id_str]

    async def current(self, agent_id: AgentId) -> ExecutionLease | None:
        now = datetime.now(UTC)
        key = str(agent_id)
        with self._lock:
            record = self._leases.get(key)
            if record is None:
                return None
            if record.expires_at_native <= now:
                # Lazy GC of expired leases on read.
                del self._leases[key]
                return None
            return record.lease

    # ----- introspection (testing / observability) ----------------------

    def active_count(self) -> int:
        """Number of currently-held leases (snapshot — caller may race)."""
        now = datetime.now(UTC)
        with self._lock:
            return sum(
                1 for rec in self._leases.values() if rec.expires_at_native > now
            )
