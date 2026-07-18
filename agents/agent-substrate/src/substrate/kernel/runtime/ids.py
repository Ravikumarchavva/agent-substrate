"""Run identity — the foundational IDs for durable runs."""

from __future__ import annotations

import uuid
from enum import Enum


RunId = str
"""Globally unique, time-sortable run identifier.

Use ``new_run_id()`` to create one.  Currently UUID4 hex; will migrate to
ULID (lexicographically sortable, millisecond-precision) in a future pass —
the type alias ensures that rename stays a one-line change.
"""


def new_run_id() -> RunId:
    """Generate a fresh RunId."""
    return uuid.uuid4().hex


class RunStatus(str, Enum):
    """Lifecycle state of a single durable run.

    Terminal states: COMPLETED, FAILED, CANCELLED.
    Non-terminal: PENDING, RUNNING, SUSPENDED.

    A run in SUSPENDED is dormant — zero RAM, zero CPU, just rows in storage.
    The SchedulerProtocol wakes it when a message, timer, signal, or child_done arrives.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


__all__ = ["RunId", "new_run_id", "RunStatus"]
