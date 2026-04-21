"""shared.observability - OpenTelemetry setup."""

from ravi.shared.observability.telemetry import (
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
