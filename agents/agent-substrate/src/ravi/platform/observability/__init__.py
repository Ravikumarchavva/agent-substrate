"""ravi.platform.observability — observability contracts + concrete impls.

Spans, replay admission, and operator kill switches: vendor-neutral Protocols
plus immutable value objects, alongside their in-memory and integration-backed
implementations. Operator-facing infrastructure, orthogonal to what agents do.
"""

from __future__ import annotations

from ravi.platform.observability._killswitch import (
    KillSwitchDecision,
    KillSwitchRule,
    KillSwitchScope,
    KillSwitchTarget,
    OperatorKillSwitch,
)
from ravi.platform.observability._replay import (
    ReplayAdmission,
    ReplayAdmissionStatus,
    ReplayDenyRule,
    ReplayGate,
    ReplayRequest,
)
from ravi.platform.observability._spans import (
    EnvelopeSpan,
    EnvelopeSpanRecorder,
    SpanQuery,
    SpanStatus,
)
from ravi.integrations.observability import OtelEnvelopeSpanRecorder
from ravi.platform.observability._in_memory import (
    InMemoryEnvelopeSpanRecorder,
    InMemoryOperatorKillSwitch,
    InMemoryReplayGate,
)

__all__ = [
    # Contracts
    "EnvelopeSpan",
    "EnvelopeSpanRecorder",
    "SpanQuery",
    "SpanStatus",
    "KillSwitchDecision",
    "KillSwitchRule",
    "KillSwitchScope",
    "KillSwitchTarget",
    "OperatorKillSwitch",
    "ReplayAdmission",
    "ReplayAdmissionStatus",
    "ReplayDenyRule",
    "ReplayGate",
    "ReplayRequest",
    # Impls
    "InMemoryEnvelopeSpanRecorder",
    "InMemoryOperatorKillSwitch",
    "InMemoryReplayGate",
    "OtelEnvelopeSpanRecorder",
]
