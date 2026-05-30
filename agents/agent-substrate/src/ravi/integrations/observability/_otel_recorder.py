"""OpenTelemetry-backed EnvelopeSpanRecorder integration.

Bridges kernel ``EnvelopeSpan`` objects to the OpenTelemetry SDK.  Spans are
exported via OTLP HTTP when ``otlp_endpoint`` is provided; otherwise they are
emitted to stdout via ``ConsoleSpanExporter``.

Local query (``span_for``, ``spans_for_envelope``, etc.) is delegated to an
``InMemoryEnvelopeSpanRecorder`` so the full query surface works without
network round-trips.

Thread-safety
~~~~~~~~~~~~~
``_otel_spans`` is guarded by ``threading.RLock``.  No lock is held across
``await``.
"""

from __future__ import annotations

import threading
from datetime import datetime

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.trace import StatusCode

from ravi.platform.observability._in_memory import InMemoryEnvelopeSpanRecorder
from ravi.platform.observability._spans import (
    EnvelopeSpan,
    SpanQuery,
    SpanStatus,
)

__all__ = ["OtelEnvelopeSpanRecorder"]

SpanAttributes = tuple[tuple[str, str], ...]

_STATUS_MAP: dict[SpanStatus, StatusCode] = {
    SpanStatus.STARTED: StatusCode.UNSET,
    SpanStatus.OK: StatusCode.OK,
    SpanStatus.FAILED: StatusCode.ERROR,
    SpanStatus.CANCELLED: StatusCode.ERROR,
}


class OtelEnvelopeSpanRecorder:
    """OpenTelemetry bridge implementing :class:`EnvelopeSpanRecorder`.

    Parameters
    ----------
    service_name:
        Value for the ``service.name`` OTel resource attribute.
    otlp_endpoint:
        If provided, spans are exported to this OTLP HTTP endpoint.
        Otherwise a ``ConsoleSpanExporter`` is used.
    """

    def __init__(
        self,
        *,
        service_name: str = "ravi-kernel",
        otlp_endpoint: str | None = None,
    ) -> None:
        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name})
        )

        if otlp_endpoint is not None:
            processor = BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint))
        else:
            processor = SimpleSpanProcessor(ConsoleSpanExporter())

        provider.add_span_processor(processor)
        self._tracer = trace.get_tracer(__name__, tracer_provider=provider)

        self._in_memory = InMemoryEnvelopeSpanRecorder()
        self._lock = threading.RLock()
        self._otel_spans: dict[str, trace.Span] = {}

    async def start_span(self, span: EnvelopeSpan) -> EnvelopeSpan:
        """Record a newly-started span and return the persisted value."""
        otel_span = self._tracer.start_span(
            span.name,
            attributes={
                "envelope.id": span.envelope_id,
                "envelope.correlation_id": span.correlation_id,
                "envelope.tenant_id": span.tenant_id,
                "envelope.event_type": span.event_type,
                "envelope.actor_id": span.actor_id,
            },
        )
        with self._lock:
            self._otel_spans[span.span_id] = otel_span

        return await self._in_memory.start_span(span)

    async def finish_span(
        self,
        span_id: str,
        *,
        status: SpanStatus = SpanStatus.OK,
        ended_at: datetime | None = None,
        attributes: SpanAttributes = (),
    ) -> EnvelopeSpan:
        """Mark ``span_id`` as finished and return the final span."""
        with self._lock:
            otel_span = self._otel_spans.pop(span_id, None)

        if otel_span is not None:
            otel_span.set_status(_STATUS_MAP.get(status, StatusCode.UNSET))
            otel_span.end()

        return await self._in_memory.finish_span(
            span_id,
            status=status,
            ended_at=ended_at,
            attributes=attributes,
        )

    async def span_for(self, span_id: str) -> EnvelopeSpan | None:
        """Return one span by id, or ``None`` when absent."""
        return await self._in_memory.span_for(span_id)

    async def spans_for_envelope(self, envelope_id: str) -> tuple[EnvelopeSpan, ...]:
        """Return all spans recorded for one envelope."""
        return await self._in_memory.spans_for_envelope(envelope_id)

    async def spans_for_correlation(
        self, correlation_id: str
    ) -> tuple[EnvelopeSpan, ...]:
        """Return all spans recorded for one correlation id."""
        return await self._in_memory.spans_for_correlation(correlation_id)

    async def query_spans(self, query: SpanQuery) -> tuple[EnvelopeSpan, ...]:
        """Return spans matching ``query``."""
        return await self._in_memory.query_spans(query)
