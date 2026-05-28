"""Observability and replay contracts for the hyperscale kernel.

The kernel surface is intentionally vendor-neutral: spans, replay admission,
and operator kill switches are Protocols plus immutable value objects. Local
or production implementations live in ``ravi.platform.observability`` and
integration packages.
"""

from __future__ import annotations

from ravi.guardrails.killswitch import (
    KillSwitchDecision,
    KillSwitchRule,
    KillSwitchScope,
    KillSwitchTarget,
    OperatorKillSwitch,
)
from ravi.platform.observability.replay import (
    ReplayAdmission,
    ReplayAdmissionStatus,
    ReplayDenyRule,
    ReplayGate,
    ReplayRequest,
)
from ravi.platform.observability.spans import (
    EnvelopeSpan,
    EnvelopeSpanRecorder,
    SpanQuery,
    SpanStatus,
)

__all__ = [
    "EnvelopeSpan",
    "EnvelopeSpanRecorder",
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
    "SpanQuery",
    "SpanStatus",
]
