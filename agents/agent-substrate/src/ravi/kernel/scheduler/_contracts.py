"""Resource Scheduler kernel contracts — Section 7.

The resource scheduler determines which agents are allowed to run, in what
order, and with what resource envelope.  The kernel defines only the
contracts; concrete implementations live in ``ravi.extensions.scheduler``.

Design principles
-----------------
* **Fair-share accounting** — each principal has a *share weight* and the
  scheduler tries to give every principal a fraction of wall-clock CPU equal
  to ``weight / total_weights``.  Principals with high instantaneous demand
  but low recent usage are favoured (dominant-resource fairness).
* **Spend authority** — the scheduler checks the :class:`BudgetLedger` before
  granting a slot.  Agents with an exhausted budget are queued (not
  dropped); they resume when a top-up arrives.
* **Preemption** — a higher-priority activation may preempt a running lower-
  priority one via a :class:`PreemptionSignal`.  The runtime checks the
  signal channel between agent steps.
* **Placement hints** — the scheduler honours :class:`PlacementContract`
  affinity / anti-affinity when multiple workers are available.

Thread-safety
~~~~~~~~~~~~~
Implementations must guard all shared scheduling state with
``threading.RLock`` so the admission path (event-loop) and heartbeat path
(background thread) interleave safely.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol, runtime_checkable

__all__ = [
    "ResourceClaim",
    "SlotGrant",
    "SlotGrantStatus",
    "PreemptionSignal",
    "PreemptionReason",
    "SchedulerContract",
    "SchedulerCapacity",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class SlotGrantStatus(Enum):
    """Outcome of a :meth:`SchedulerContract.request_slot` call."""

    GRANTED = auto()
    """Slot is immediately available; the agent may start executing."""

    QUEUED = auto()
    """Capacity is exhausted; the caller is queued and should poll or await."""

    DENIED = auto()
    """Budget exhausted or policy rejection; the caller should back off."""


class PreemptionReason(Enum):
    """Why a running slot was preempted."""

    HIGHER_PRIORITY_ARRIVAL = auto()
    BUDGET_EXHAUSTED = auto()
    OPERATOR_KILL = auto()
    IDLE_TIMEOUT = auto()


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResourceClaim:
    """Resources an agent wishes to consume for one activation.

    ``token_budget`` and ``step_budget`` mirror :class:`ExecutionLease`;
    they bound the cost of this particular activation.  ``share_weight``
    is the principal's relative scheduling weight (default 1.0 = equal share).
    """

    principal_fqn: str
    """Fully-qualified name of the principal requesting the slot."""
    token_budget: int = 0
    """Max token budget for this activation (0 = unlimited)."""
    step_budget: int = 0
    """Max tool-call steps (0 = unlimited)."""
    share_weight: float = 1.0
    """Relative scheduling weight — higher = more CPU share."""
    priority: int = 0
    """Higher value = higher urgency within the same share class."""
    placement_region: str | None = None
    """Optional region preference for multi-region deployments."""


@dataclass(frozen=True, slots=True)
class SlotGrant:
    """Outcome of a successful slot request.

    ``grant_id`` is a handle used to :meth:`SchedulerContract.release_slot`.
    ``granted_tokens`` and ``granted_steps`` may be lower than requested when
    the scheduler enforces per-activation ceilings.
    """

    grant_id: str
    principal_fqn: str
    status: SlotGrantStatus
    granted_at: str  # ISO-8601
    granted_tokens: int = 0
    granted_steps: int = 0
    queue_position: int | None = None
    """Only set when status == QUEUED."""


@dataclass(frozen=True, slots=True)
class PreemptionSignal:
    """Signal emitted to a running agent requesting it to yield its slot.

    The runtime polls :meth:`SchedulerContract.check_preemption` between
    agent steps and yields as soon as a preemption is pending.
    """

    grant_id: str
    reason: PreemptionReason
    issued_at: str  # ISO-8601
    message: str = ""


@dataclass(frozen=True, slots=True)
class SchedulerCapacity:
    """Point-in-time snapshot of scheduler load — useful for operators."""

    total_slots: int
    active_slots: int
    queued_claims: int
    utilization: float  # active_slots / total_slots


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class SchedulerContract(Protocol):
    """Fair-share resource scheduler — controls agent slot admission.

    Implementations must:

    * Serialise concurrent :meth:`request_slot` / :meth:`release_slot` calls
      so the ``active_slots`` invariant is never violated.
    * Check budget authority via the economic plane before granting.
    * Emit :class:`PreemptionSignal` when a higher-priority claim arrives
      for a full pool.
    * Be safe to call from both asyncio tasks and background threads (no GIL
      assumption — guard with ``threading.RLock``).
    """

    async def request_slot(self, claim: ResourceClaim) -> SlotGrant:
        """Request a scheduling slot for ``claim``.

        Returns :class:`SlotGrant` with ``status=GRANTED`` immediately when a
        slot is free and the principal has budget.  Returns ``QUEUED`` when
        capacity is full; the caller should call :meth:`wait_for_slot` or
        poll.  Returns ``DENIED`` when budget is exhausted or policy rejects
        the claim.
        """
        ...

    async def release_slot(self, grant_id: str) -> None:
        """Release a previously granted slot.

        Dequeues the next waiting :class:`ResourceClaim` if any.  No-op when
        ``grant_id`` is unknown or already released.
        """
        ...

    async def check_preemption(self, grant_id: str) -> PreemptionSignal | None:
        """Return a pending :class:`PreemptionSignal` or ``None`` if clear.

        The runtime calls this between agent steps; if non-None, the agent
        should checkpoint and yield its slot.
        """
        ...

    async def capacity(self) -> SchedulerCapacity:
        """Return a point-in-time capacity snapshot."""
        ...

    async def set_share_weight(
        self, principal_fqn: str, weight: float
    ) -> None:
        """Update the fair-share weight for a principal.

        Applies from the next scheduling decision onward.  Raise
        :class:`ValueError` on non-positive ``weight``.
        """
        ...
