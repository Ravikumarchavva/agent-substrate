"""SchedulerProtocol — work-queue, leasing, and admission control.

The SchedulerProtocol is the coordination layer between the durable stores (EventLogProtocol,
InboxProtocol) and the stateless Workers.  It knows *which* runs need attention and
*which* workers should handle them — but it never runs agent logic itself.

Responsibilities
----------------
- Accept enqueue requests (from InboxProtocol delivery, timer fires, signal fires,
  child completions).
- Issue leases to workers that poll for work.
- Track heartbeats and reclaim leases from dead workers.
- Enforce per-tenant fairness and backpressure.
- Coalesce multiple wakeup sources for the same run_id into one enqueue.

Coalescing guarantee
--------------------
If a timer fires AND a message arrives while a run is already in the pending
queue (or active), the SchedulerProtocol does NOT create a second queue entry.  It
merges the new wakeup trigger into the existing pending entry.  Workers receive
a run at most once per wake-cycle regardless of how many sources fired.

Dead-run retry policy
---------------------
When a worker releases a run with status=FAILED, the SchedulerProtocol consults
``RunRetryPolicy`` to decide whether to re-enqueue, move to dead-run, or
escalate.  The policy is passed at enqueue time and stored with the queue
entry.

Three-tier topology role
------------------------
SchedulerProtocol sits between Gateway (enqueues) and Workers (lease).  It is the
valve that prevents a viral agent from melting the cluster — per-tenant quotas
and backpressure live here, not in the Gateway or Workers.
"""

from __future__ import annotations

from datetime import datetime
from typing import AsyncIterator, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from substrate.kernel.abstractions.agent.supervision import Priority
from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.runtime.ids import RunId, RunStatus
from substrate.kernel.abstractions.runtime.wakeup import Wakeup


class RunRetryPolicy(BaseModel):
    """Policy governing automatic retries when a run terminates with FAILED.

    ``max_retries``   — how many times to re-enqueue before moving to dead-run.
    ``backoff_s``      — base delay before the first retry; doubles each
                         subsequent attempt (``backoff_s * 2**(retry_count-1)``),
                         capped at ``max_backoff_s``. Implemented by parking
                         the run ``suspended`` with a ``wake_at`` timer — the
                         same mechanism a timed suspension already rides — so
                         a retry backoff survives a process restart exactly
                         like any other legitimate dormancy.
    ``max_backoff_s``  — ceiling on the exponential backoff delay.
    ``dead_run_on_cancel`` — if ``True``, a CANCELLED run is also moved to
                             dead-run storage; default ``False``.
    """

    max_retries: int = Field(default=3, ge=0)
    backoff_s: float = Field(default=5.0, ge=0)
    max_backoff_s: float = Field(default=300.0, ge=0)
    dead_run_on_cancel: bool = False

    model_config = {"frozen": True}


__all__ = ["RunRetryPolicy"]
