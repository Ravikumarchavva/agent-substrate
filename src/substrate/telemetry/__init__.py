"""substrate.telemetry — Spans and metrics at every chokepoint of the engine."""

from __future__ import annotations

from substrate.telemetry.metrics import (
    Instruments,
    instruments,
)
from substrate.telemetry.tracing import (
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
    "span",
]
