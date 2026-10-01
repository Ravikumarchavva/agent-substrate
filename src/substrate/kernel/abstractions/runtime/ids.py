"""Run identity and lifecycle status."""

from __future__ import annotations

from enum import StrEnum

from substrate.kernel.abstractions.ids import RunId, new_run_id


class RunStatus(StrEnum):
    """Lifecycle state of a single durable run.

    Terminal: COMPLETED, FAILED, CANCELLED. Non-terminal: PENDING, RUNNING, SUSPENDED.

    A SUSPENDED run is dormant — zero RAM, zero CPU, just rows in storage.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED)


__all__ = ["RunId", "RunStatus", "new_run_id"]
