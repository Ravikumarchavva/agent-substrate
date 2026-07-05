"""Runtime metrics — OpenTelemetry counters shared by both Scheduler backends.

Uses the raw ``opentelemetry.metrics`` API directly (not
``serving.shared.observability``'s ``Metrics`` wrapper, which is server-only
setup) so this stays usable from a bare ``pip install agent-substrate`` —
before ``configure_opentelemetry()`` is ever called, ``metrics.get_meter()``
returns a no-op meter, so these calls are always safe and zero-overhead by
default; they only start emitting once a caller configures OTel.

Only ``retries`` and ``suspensions`` are instrumented here — both are natural
counters incremented at a single, well-defined call site in ``release()``.
Queue depth and lease age are gauges that need periodic polling rather than a
call-site increment; deliberately left for later (see the roadmap docs)
rather than rushed into a per-backend polling loop here.
"""

from __future__ import annotations

from opentelemetry import metrics

_meter = metrics.get_meter("substrate.runtime.scheduler")

retry_counter = _meter.create_counter(
    "substrate.runtime.retries",
    description="Runs re-enqueued after a retryable FAILED release, by backend.",
)
suspension_counter = _meter.create_counter(
    "substrate.runtime.suspensions",
    description="Runs parked SUSPENDED on release, by backend.",
)

__all__ = ["retry_counter", "suspension_counter"]
