"""Tests for OtelEnvelopeSpanRecorder.

All OTel SDK calls are mocked — no live OTLP endpoint required.

Coverage
--------
- Protocol conformance (isinstance check)
- start_span returns EnvelopeSpan
- finish_span marks span as finished (is_finished=True)
- span_for returns the recorded span
- spans_for_envelope returns correct spans
- query_spans filters by tenant_id
- With otlp_endpoint: OTLPSpanExporter is created
- Without endpoint: ConsoleSpanExporter is created
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from ravi.integrations.observability import OtelEnvelopeSpanRecorder
from ravi.kernel.observability import (
    EnvelopeSpan,
    EnvelopeSpanRecorder,
    SpanQuery,
    SpanStatus,
)

UTC = timezone.utc


def _make_span(
    envelope_id: str = "env-1",
    correlation_id: str = "corr-1",
    tenant_id: str = "t1",
    name: str = "test-span",
) -> EnvelopeSpan:
    return EnvelopeSpan(
        envelope_id=envelope_id,
        correlation_id=correlation_id,
        name=name,
        tenant_id=tenant_id,
        event_type="test.event",
        actor_id="actor-1",
    )


@pytest.fixture
def recorder() -> OtelEnvelopeSpanRecorder:
    """Return a recorder with console exporter (no OTLP endpoint)."""
    return OtelEnvelopeSpanRecorder(service_name="test-service")


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestProtocolConformance:
    def test_isinstance_envelope_span_recorder(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        assert isinstance(recorder, EnvelopeSpanRecorder)


# ===========================================================================
# start_span
# ===========================================================================


class TestStartSpan:
    async def test_start_span_returns_envelope_span(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        span = _make_span()
        result = await recorder.start_span(span)
        assert isinstance(result, EnvelopeSpan)
        assert result.envelope_id == span.envelope_id

    async def test_start_span_stores_otel_span_in_dict(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        span = _make_span()
        await recorder.start_span(span)
        assert span.span_id in recorder._otel_spans

    async def test_start_span_preserves_span_id(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        span = _make_span()
        result = await recorder.start_span(span)
        assert result.span_id == span.span_id


# ===========================================================================
# finish_span
# ===========================================================================


class TestFinishSpan:
    async def test_finish_span_marks_span_as_finished(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        span = _make_span()
        await recorder.start_span(span)
        finished = await recorder.finish_span(span.span_id)
        assert finished.is_finished

    async def test_finish_span_sets_ok_status_by_default(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        span = _make_span()
        await recorder.start_span(span)
        finished = await recorder.finish_span(span.span_id)
        assert finished.status is SpanStatus.OK

    async def test_finish_span_sets_failed_status(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        span = _make_span()
        await recorder.start_span(span)
        finished = await recorder.finish_span(span.span_id, status=SpanStatus.FAILED)
        assert finished.status is SpanStatus.FAILED

    async def test_finish_span_removes_otel_span_from_dict(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        span = _make_span()
        await recorder.start_span(span)
        assert span.span_id in recorder._otel_spans
        await recorder.finish_span(span.span_id)
        assert span.span_id not in recorder._otel_spans

    async def test_finish_span_with_ended_at(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        span = _make_span()
        await recorder.start_span(span)
        ts = datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)
        finished = await recorder.finish_span(span.span_id, ended_at=ts)
        assert finished.ended_at == ts


# ===========================================================================
# span_for
# ===========================================================================


class TestSpanFor:
    async def test_span_for_returns_recorded_span(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        span = _make_span()
        await recorder.start_span(span)
        found = await recorder.span_for(span.span_id)
        assert found is not None
        assert found.span_id == span.span_id

    async def test_span_for_returns_none_for_unknown_id(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        result = await recorder.span_for("nonexistent-span-id")
        assert result is None


# ===========================================================================
# spans_for_envelope
# ===========================================================================


class TestSpansForEnvelope:
    async def test_spans_for_envelope_returns_all_spans(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        span1 = _make_span(envelope_id="env-x", name="span-1")
        span2 = _make_span(envelope_id="env-x", name="span-2")
        span3 = _make_span(envelope_id="env-other", name="span-3")
        await recorder.start_span(span1)
        await recorder.start_span(span2)
        await recorder.start_span(span3)
        result = await recorder.spans_for_envelope("env-x")
        span_ids = {s.span_id for s in result}
        assert span1.span_id in span_ids
        assert span2.span_id in span_ids
        assert span3.span_id not in span_ids

    async def test_spans_for_envelope_empty_when_no_match(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        result = await recorder.spans_for_envelope("nonexistent-env")
        assert result == ()


# ===========================================================================
# spans_for_correlation
# ===========================================================================


class TestSpansForCorrelation:
    async def test_spans_for_correlation_returns_matching_spans(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        span = _make_span(correlation_id="corr-abc")
        await recorder.start_span(span)
        result = await recorder.spans_for_correlation("corr-abc")
        assert len(result) == 1
        assert result[0].span_id == span.span_id


# ===========================================================================
# query_spans
# ===========================================================================


class TestQuerySpans:
    async def test_query_spans_filters_by_tenant_id(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        span_t1 = _make_span(tenant_id="tenant-1")
        span_t2 = _make_span(
            envelope_id="env-2", correlation_id="corr-2", tenant_id="tenant-2"
        )
        await recorder.start_span(span_t1)
        await recorder.start_span(span_t2)

        query = SpanQuery(tenant_id="tenant-1")
        result = await recorder.query_spans(query)
        assert all(s.tenant_id == "tenant-1" for s in result)
        ids = {s.span_id for s in result}
        assert span_t1.span_id in ids
        assert span_t2.span_id not in ids

    async def test_query_spans_returns_empty_for_no_match(
        self, recorder: OtelEnvelopeSpanRecorder
    ) -> None:
        query = SpanQuery(tenant_id="nonexistent-tenant")
        result = await recorder.query_spans(query)
        assert result == ()


# ===========================================================================
# Exporter selection
# ===========================================================================


class TestExporterSelection:
    def test_console_exporter_used_without_endpoint(self) -> None:
        """When no otlp_endpoint is given, ConsoleSpanExporter should be used."""
        with patch(
            "ravi.integrations.observability._otel_recorder.SimpleSpanProcessor"
        ) as mock_simple, patch(
            "ravi.integrations.observability._otel_recorder.ConsoleSpanExporter"
        ) as mock_console:
            mock_simple.return_value = MagicMock()
            mock_console.return_value = MagicMock()
            OtelEnvelopeSpanRecorder(service_name="svc")
            mock_console.assert_called_once()

    def test_otlp_exporter_used_with_endpoint(self) -> None:
        """When otlp_endpoint is provided, OTLPSpanExporter should be used."""
        with patch(
            "ravi.integrations.observability._otel_recorder.BatchSpanProcessor"
        ) as mock_batch, patch(
            "ravi.integrations.observability._otel_recorder.OTLPSpanExporter"
        ) as mock_otlp:
            mock_batch.return_value = MagicMock()
            mock_otlp.return_value = MagicMock()
            OtelEnvelopeSpanRecorder(
                service_name="svc", otlp_endpoint="http://localhost:4318"
            )
            mock_otlp.assert_called_once_with(endpoint="http://localhost:4318")
