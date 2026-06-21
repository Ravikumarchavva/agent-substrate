"""agent_substrate.serving.shared.observability — telemetry and distributed tracing."""

from __future__ import annotations

from agent_substrate.serving.shared.observability.telemetry import (
    configure_opentelemetry,
    shutdown_opentelemetry,
    Tracer,
    Metrics,
    global_tracer,
    global_metrics,
    logger,
)

__all__ = [
    "configure_opentelemetry",
    "shutdown_opentelemetry",
    "Tracer",
    "Metrics",
    "global_tracer",
    "global_metrics",
    "logger",
]
