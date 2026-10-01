"""substrate.kernel.telemetry — observability the engine owns.

One mechanism, instrumented at the points where work actually happens. See
``tracing`` for why spans are built the way they are, ``semconv`` for the names,
and ``metrics`` for the instruments.
"""

from __future__ import annotations

from substrate.kernel.telemetry import semconv
from substrate.kernel.telemetry.metrics import Instruments, instruments
from substrate.kernel.telemetry.tracing import (
    SpanHandle,
    capture_content,
    current_trace_context,
    span,
)

__all__ = [
    "Instruments",
    "SpanHandle",
    "capture_content",
    "current_trace_context",
    "instruments",
    "semconv",
    "span",
]
