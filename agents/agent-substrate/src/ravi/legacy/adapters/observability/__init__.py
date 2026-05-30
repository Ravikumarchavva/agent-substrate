"""ravi.adapters.observability — observability contracts + concrete impls.

Envelope spans and replay admission: vendor-neutral Protocols plus immutable
value objects, alongside their in-memory and OTel-backed implementations.
Operator-facing infrastructure, orthogonal to what agents do.
"""

from __future__ import annotations

from ravi.adapters.observability._spans import (
    EnvelopeSpan,
    EnvelopeSpanRecorder,
    SpanQuery,
    SpanStatus,
)
from ravi.adapters.observability._replay import (
    ReplayAdmission,
    ReplayAdmissionStatus,
    ReplayDenyRule,
    ReplayGate,
    ReplayRequest,
)
from ravi.adapters.observability._in_memory import (
    InMemoryEnvelopeSpanRecorder,
    InMemoryReplayGate,
)
from ravi.adapters.observability._otel_recorder import OtelEnvelopeSpanRecorder

__all__ = [
    # Contracts
    "EnvelopeSpan",
    "EnvelopeSpanRecorder",
    "SpanQuery",
    "SpanStatus",
    "ReplayAdmission",
    "ReplayAdmissionStatus",
    "ReplayDenyRule",
    "ReplayGate",
    "ReplayRequest",
    # Impls
    "InMemoryEnvelopeSpanRecorder",
    "InMemoryReplayGate",
    "OtelEnvelopeSpanRecorder",
]
