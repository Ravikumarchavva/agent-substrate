"""ravi.platform.observability — concrete recorders, gates, and kill switches.

The contracts (Protocols + value objects: ``EnvelopeSpan``, ``ReplayGate``,
``OperatorKillSwitch``, …) live in :mod:`ravi.kernel.observability`. This
package holds only the concrete implementations.
"""

from __future__ import annotations

from ravi.integrations.observability import OtelEnvelopeSpanRecorder
from ravi.platform.observability._in_memory import (
    InMemoryEnvelopeSpanRecorder,
    InMemoryOperatorKillSwitch,
    InMemoryReplayGate,
)

__all__ = [
    "InMemoryEnvelopeSpanRecorder",
    "InMemoryOperatorKillSwitch",
    "InMemoryReplayGate",
    "OtelEnvelopeSpanRecorder",
]
