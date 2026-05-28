"""In-process fair-share scheduler — reference implementation of Section 7.

Implements :class:`SchedulerContract` without external infrastructure using
dominant-resource fair scheduling:

Algorithm
---------
Each principal has a *share weight* (default 1.0).  When a new slot request
arrives, the scheduler grants it if ``active_slots < max_slots``.  On a full
pool the scheduler optionally preempts the running agent with the lowest
*dominant resource score* (= ``weight / running_activations``) when the
incoming claim has a higher priority.

Preemption
----------
``check_preemption(grant_id)`` returns a :class:`PreemptionSignal` when the
scheduler has marked that grant for preemption.  The caller (runtime) is
expected to honor it between steps; the scheduler does not forcibly cancel
anything.

Thread-safety
-------------
All mutable state is guarded by a single ``threading.RLock``.  No lock is
held across ``await``.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Deque

from ravi.platform.scheduling._contracts import (
    PreemptionReason,
    PreemptionSignal,
    ResourceClaim,
    SchedulerCapacity,
    SlotGrant,
    SlotGrantStatus,
)

__all__ = ["InMemoryFairShareScheduler"]

UTC = timezone.utc
_DEFAULT_MAX_SLOTS: int = 64
_DEFAULT_WEIGHT: float = 1.0


def _iso_now() -> str:
    return datetime.now(UTC).isoformat()


class _ActiveGrant:
    """Internal record for a running slot."""

    __slots__ = ("grant_id", "principal_fqn", "weight", "priority", "granted_at", "gpu_required")

    def __init__(
        self,
        grant_id: str,
        principal_fqn: str,
        weight: float,
        priority: int,
        gpu_required: bool = False,
    ) -> None:
        self.grant_id = grant_id
        self.principal_fqn = principal_fqn
        self.weight = weight
        self.priority = priority
        self.granted_at = datetime.now(UTC)
        self.gpu_required = gpu_required


class InMemoryFairShareScheduler:
    """Thread-safe in-process fair-share scheduler.

    Parameters
    ----------
    max_slots:
        Total concurrent activation slots (default 64).
    allow_preemption:
        When ``True``, a higher-priority claim may preempt the active grant
        with the lowest dominance score when the pool is full.
    """

    def __init__(
        self,
        *,
        max_slots: int = _DEFAULT_MAX_SLOTS,
        max_gpu_slots: int = 4,
        allow_preemption: bool = False,
    ) -> None:
        if max_slots <= 0:
            raise ValueError(f"max_slots must be > 0, got {max_slots!r}")
        self._max_slots = max_slots
        self._max_gpu_slots = max_gpu_slots
        self._allow_preemption = allow_preemption
        self._lock = threading.RLock()
        # grant_id → _ActiveGrant
        self._active: dict[str, _ActiveGrant] = {}
        # FIFO queue of waiting (claim, grant_id)
        self._queue: Deque[tuple[ResourceClaim, str]] = deque()
        # preemption signals: grant_id → PreemptionSignal
        self._preemptions: dict[str, PreemptionSignal] = {}
        # per-principal share weights
        self._weights: dict[str, float] = {}
        # grant_id → asyncio.Event
        self._events: dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------
    # SchedulerContract protocol
    # ------------------------------------------------------------------

    async def request_slot(self, claim: ResourceClaim) -> SlotGrant:
        """Request a scheduling slot for ``claim``."""
        if claim.share_weight <= 0:
            raise ValueError(
                f"share_weight must be > 0, got {claim.share_weight!r}"
            )

        grant_id = uuid.uuid4().hex
        with self._lock:
            weight = self._weights.get(claim.principal_fqn, claim.share_weight)
            active_gpu = sum(1 for g in self._active.values() if g.gpu_required)

            if len(self._active) < self._max_slots and (not claim.gpu_required or active_gpu < self._max_gpu_slots):
                # Pool has capacity — grant immediately.
                self._active[grant_id] = _ActiveGrant(
                    grant_id=grant_id,
                    principal_fqn=claim.principal_fqn,
                    weight=weight,
                    priority=claim.priority,
                    gpu_required=claim.gpu_required,
                )
                return SlotGrant(
                    grant_id=grant_id,
                    principal_fqn=claim.principal_fqn,
                    status=SlotGrantStatus.GRANTED,
                    granted_at=_iso_now(),
                    granted_tokens=claim.token_budget,
                    granted_steps=claim.step_budget,
                )

            # Pool full — try preemption if enabled.
            if self._allow_preemption and self._active:
                victim = self._find_preemption_victim_locked(claim)
                if victim is not None:
                    self._preemptions[victim.grant_id] = PreemptionSignal(
                        grant_id=victim.grant_id,
                        reason=PreemptionReason.HIGHER_PRIORITY_ARRIVAL,
                        issued_at=_iso_now(),
                        message=(
                            f"preempted by {claim.principal_fqn}"
                            f" (priority={claim.priority})"
                        ),
                    )
                    del self._active[victim.grant_id]
                    self._active[grant_id] = _ActiveGrant(
                        grant_id=grant_id,
                        principal_fqn=claim.principal_fqn,
                        weight=weight,
                        priority=claim.priority,
                        gpu_required=claim.gpu_required,
                    )
                    return SlotGrant(
                        grant_id=grant_id,
                        principal_fqn=claim.principal_fqn,
                        status=SlotGrantStatus.GRANTED,
                        granted_at=_iso_now(),
                        granted_tokens=claim.token_budget,
                        granted_steps=claim.step_budget,
                    )

            # Enqueue the claim.
            queue_pos = len(self._queue)
            self._queue.append((claim, grant_id))
            self._events[grant_id] = asyncio.Event()
            return SlotGrant(
                grant_id=grant_id,
                principal_fqn=claim.principal_fqn,
                status=SlotGrantStatus.QUEUED,
                granted_at=_iso_now(),
                queue_position=queue_pos,
            )

    async def release_slot(self, grant_id: str) -> None:
        """Release a slot and promote the first queued claim if any."""
        with self._lock:
            self._active.pop(grant_id, None)
            self._preemptions.pop(grant_id, None)

            # Promote waiting claims from the queue that fit
            temp_queue = deque(self._queue)
            self._queue.clear()

            while temp_queue:
                queued_claim, queued_grant_id = temp_queue.popleft()
                active_gpu = sum(1 for g in self._active.values() if g.gpu_required)

                if len(self._active) < self._max_slots and (not queued_claim.gpu_required or active_gpu < self._max_gpu_slots):
                    # Promote
                    weight = self._weights.get(
                        queued_claim.principal_fqn, queued_claim.share_weight
                    )
                    self._active[queued_grant_id] = _ActiveGrant(
                        grant_id=queued_grant_id,
                        principal_fqn=queued_claim.principal_fqn,
                        weight=weight,
                        priority=queued_claim.priority,
                        gpu_required=queued_claim.gpu_required,
                    )
                    event = self._events.pop(queued_grant_id, None)
                    if event is not None:
                        event.set()
                else:
                    self._queue.append((queued_claim, queued_grant_id))

    async def wait_for_slot(self, grant_id: str) -> None:
        """Suspend execution until the queued grant ``grant_id`` is promoted to active."""
        event = None
        with self._lock:
            if grant_id in self._active:
                return
            event = self._events.get(grant_id)

        if event is not None:
            await event.wait()

    async def check_preemption(self, grant_id: str) -> PreemptionSignal | None:
        """Return a pending preemption signal or ``None`` if clear."""
        with self._lock:
            return self._preemptions.get(grant_id)

    async def capacity(self) -> SchedulerCapacity:
        """Return a point-in-time capacity snapshot."""
        with self._lock:
            active = len(self._active)
            queued = len(self._queue)
            utilization = active / self._max_slots if self._max_slots else 0.0
            return SchedulerCapacity(
                total_slots=self._max_slots,
                active_slots=active,
                queued_claims=queued,
                utilization=utilization,
            )

    async def set_share_weight(
        self, principal_fqn: str, weight: float
    ) -> None:
        """Update the fair-share weight for a principal."""
        if weight <= 0:
            raise ValueError(f"weight must be > 0, got {weight!r}")
        with self._lock:
            self._weights[principal_fqn] = weight

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_preemption_victim_locked(
        self, incoming: ResourceClaim
    ) -> _ActiveGrant | None:
        """Return the active grant eligible for preemption, or ``None``.

        Victim selection: lowest-priority grant among those whose priority
        is strictly below the incoming claim's priority.  If multiple grants
        share the minimum priority, pick the one with the lowest dominance
        score (weight / running activations for that principal).
        """
        candidates = [
            g for g in self._active.values() if g.priority < incoming.priority
        ]
        if not candidates:
            return None

        # Count running activations per principal for dominance scoring.
        run_count: dict[str, int] = {}
        for g in self._active.values():
            run_count[g.principal_fqn] = run_count.get(g.principal_fqn, 0) + 1

        def _dominance(g: _ActiveGrant) -> float:
            count = run_count.get(g.principal_fqn, 1)
            return g.weight / max(count, 1)

        return min(candidates, key=lambda g: (g.priority, _dominance(g)))
