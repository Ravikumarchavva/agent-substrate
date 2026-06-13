"""Scheduler — work-queue, leasing, and admission control.

The Scheduler is the coordination layer between the durable stores (EventLog,
Inbox) and the stateless Workers.  It knows *which* runs need attention and
*which* workers should handle them — but it never runs agent logic itself.

Responsibilities
----------------
- Accept enqueue requests (from Inbox delivery, timer fires, signal fires,
  child completions).
- Issue leases to workers that poll for work.
- Track heartbeats and reclaim leases from dead workers.
- Enforce per-tenant fairness and backpressure.
- Coalesce multiple wakeup sources for the same run_id into one enqueue.

Coalescing guarantee
--------------------
If a timer fires AND a message arrives while a run is already in the pending
queue (or active), the Scheduler does NOT create a second queue entry.  It
merges the new wakeup trigger into the existing pending entry.  Workers receive
a run at most once per wake-cycle regardless of how many sources fired.

Dead-run retry policy
---------------------
When a worker releases a run with status=FAILED, the Scheduler consults
``RunRetryPolicy`` to decide whether to re-enqueue, move to dead-run, or
escalate.  The policy is passed at enqueue time and stored with the queue
entry.

Three-tier topology role
------------------------
Scheduler sits between Gateway (enqueues) and Workers (lease).  It is the
valve that prevents a viral agent from melting the cluster — per-tenant quotas
and backpressure live here, not in the Gateway or Workers.
"""

from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Protocol

from pydantic import BaseModel

from ravi.kernel.runtime.ids import RunId, RunStatus
from ravi.kernel.runtime.wakeup import Wakeup


class RunRetryPolicy(BaseModel):
    """Policy governing automatic retries when a run terminates with FAILED.

    ``max_retries`` — how many times to re-enqueue before moving to dead-run.
    ``backoff_s``   — seconds to wait before the next retry (flat for now;
                      exponential backoff is an implementation concern).
    ``dead_run_on_cancel`` — if ``True``, a CANCELLED run is also moved to
                             dead-run storage; default ``False``.
    """

    max_retries: int = 3
    backoff_s: float = 5.0
    dead_run_on_cancel: bool = False

    model_config = {"frozen": True}


class Lease(BaseModel):
    """A time-limited grant for a worker to process a specific run.

    When a lease expires (worker crashed / timed out), the Scheduler
    reclaims it and re-enqueues the run for another worker to pick up.
    Workers must call ``heartbeat`` periodically to renew their lease.
    """

    run_id: RunId
    worker_id: str
    expires_at: datetime
    attempt: int = 0

    model_config = {"frozen": True}


class Scheduler(Protocol):
    """Work-queue, leasing, and admission control for durable runs.

    Implementations: single-process asyncio priority queue (Stage 0),
    Postgres ``SELECT FOR UPDATE SKIP LOCKED`` (Stage 1), Redis / NATS
    JetStream work-queue (Stage 2+), distributed consistent-hash scheduler
    (Stage 3).

    Semantic guarantees
    -------------------
    - ``enqueue`` for an already-pending run_id is a no-op (coalescing):
      the Wakeup trigger is merged into the existing entry; no duplicate
      worker dispatch.
    - ``lease`` returns at most ``capacity`` leases and never returns a
      run_id that is already leased to another worker.
    - A lease whose ``expires_at`` has passed is automatically reclaimed
      and the run re-enqueued without manual intervention.
    - ``release`` with a terminal status moves the run out of the active
      queue (and into dead-run storage if retries are exhausted).
    """

    async def enqueue(
        self,
        run_id: RunId,
        *,
        priority: int,
        tenant: str,
        wake: Wakeup | None = None,
        retry_policy: RunRetryPolicy | None = None,
    ) -> None:
        """Add ``run_id`` to the work-queue (or coalesce into existing entry).

        ``priority`` is an integer weight (see ``kernel/supervision.py::Priority``).
        ``tenant`` is used for per-tenant fairness and quota enforcement.
        ``wake`` is the trigger that caused this enqueue (informational for
        the worker when it drains the wakeup reason).
        """
        ...

    async def lease(
        self,
        *,
        worker_id: str,
        capacity: int,
    ) -> list[Lease]:
        """Claim up to ``capacity`` pending runs for ``worker_id``.

        Returns immediately with whatever is available (may be empty).
        Workers should poll in a tight loop with a short sleep when empty.
        Implementations MAY use work-stealing or consistent hashing to
        prefer locality (Stage 2+).
        """
        ...

    async def heartbeat(self, lease: Lease) -> None:
        """Renew the expiry on ``lease`` to prove the worker is still alive.

        Workers must call this at least once per (``expires_at`` − now) / 2
        interval.  A missing heartbeat causes the Scheduler to reclaim the
        lease and re-enqueue the run.
        """
        ...

    async def release(
        self,
        lease: Lease,
        *,
        status: RunStatus,
        wake_on: Wakeup | None = None,
    ) -> None:
        """Return the lease and record the run's new status.

        ``status=SUSPENDED`` + ``wake_on`` schedules the next wakeup trigger.
        ``status=COMPLETED | FAILED | CANCELLED`` moves the run to terminal
        storage (and triggers retry logic for FAILED per the retry policy).
        ``status=RUNNING`` should not be passed to release — that is the
        lease's in-flight state.
        """
        ...

    async def pending_runs(
        self,
        *,
        tenant: str | None = None,
    ) -> AsyncIterator[RunId]:
        """Yield run_ids currently in the pending queue (for monitoring)."""
        ...


__all__ = ["RunRetryPolicy", "Lease", "Scheduler"]
