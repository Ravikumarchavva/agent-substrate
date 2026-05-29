"""ravi.kernel.scheduler — Resource-scheduling contracts.

Pure contracts (Protocols + value objects + enums). Concrete schedulers live in
:mod:`ravi.platform.scheduling` and :mod:`ravi.integrations.scheduler`.
"""

from __future__ import annotations

from ravi.kernel.scheduler._contracts import (
    PreemptionReason,
    PreemptionSignal,
    ResourceClaim,
    SchedulerCapacity,
    SchedulerContract,
    SlotGrant,
    SlotGrantStatus,
)

__all__ = [
    "PreemptionReason",
    "PreemptionSignal",
    "ResourceClaim",
    "SchedulerCapacity",
    "SchedulerContract",
    "SlotGrant",
    "SlotGrantStatus",
]
