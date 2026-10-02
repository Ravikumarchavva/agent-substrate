"""The span helper, tested against a real OpenTelemetry SDK with an in-memory exporter.

These assert the properties the invariant register depends on (I24, I25): spans
nest, outcomes survive, suspension is not a failure, a trace continues across a
process boundary, and content never leaks onto a span by default.
"""

from __future__ import annotations

import pytest

pytest.importorskip("opentelemetry.sdk")

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from substrate.types import TraceContext
from substrate.types import RateLimitedError, SuspendInterrupt
from substrate.types import Wakeup
from substrate.telemetry import semconv
from substrate.telemetry import span


@pytest.fixture
def exporter(monkeypatch: pytest.MonkeyPatch) -> InMemorySpanExporter:
    exp = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exp))
    # set_tracer_provider is once-per-process; patch the accessor instead so
    # each test gets a clean exporter.
    monkeypatch.setattr(trace, "get_tracer", lambda name, *a, **k: provider.get_tracer(name))
    return exp


def _by_name(exp: InMemorySpanExporter) -> dict[str, object]:
    return {s.name: s for s in exp.get_finished_spans()}


def test_spans_nest_into_one_trace(exporter: InMemorySpanExporter) -> None:
    with span("run"):
        with span("llm"):
            pass
        with span("tool"):
            with span("tool.inner"):
                pass
    spans = _by_name(exporter)
    assert len({s.context.trace_id for s in spans.values()}) == 1
    assert spans["run"].parent is None
    assert spans["llm"].parent.span_id == spans["run"].context.span_id
    assert spans["tool.inner"].parent.span_id == spans["tool"].context.span_id


def test_outcome_set_inside_the_block_is_recorded(exporter: InMemorySpanExporter) -> None:
    with span("tool") as handle:
        handle.set_attribute(semconv.TOOL_OUTCOME, "ok")
    assert dict(_by_name(exporter)["tool"].attributes)[semconv.TOOL_OUTCOME] == "ok"


def test_a_failure_marks_the_span_and_carries_the_error_code(exporter: InMemorySpanExporter) -> None:
    with pytest.raises(RateLimitedError):
        with span("llm"):
            raise RateLimitedError(retry_after=2.0)
    s = _by_name(exporter)["llm"]
    assert s.status.status_code == StatusCode.ERROR
    assert dict(s.attributes)[semconv.ERROR_CODE] == "rate_limited"
    assert dict(s.attributes)[semconv.ERROR_RETRYABLE] is True


def test_suspension_is_not_a_failure(exporter: InMemorySpanExporter) -> None:
    """A run that goes dormant did not fail; alerting on it would page someone for
    every human-in-the-loop wait."""
    wakeup = Wakeup(kind="signal", signals=["hitl:1"])
    with pytest.raises(SuspendInterrupt):
        with span("run"):
            raise SuspendInterrupt("r", wakeup)
    s = _by_name(exporter)["run"]
    assert s.status.status_code != StatusCode.ERROR
    assert dict(s.attributes)[semconv.SUSPENDED] is True


def test_a_trace_continues_across_a_process_boundary(exporter: InMemorySpanExporter) -> None:
    """The persisted context is all a different worker has. The span it opens must
    land in the same trace, parented to where the first worker left off."""
    with span("lease.one") as first:
        persisted = first.context()
    assert persisted is not None
    revived = TraceContext.from_traceparent(persisted.to_traceparent())  # as read back from storage

    with span("lease.two", parent=revived, links=[persisted]):
        pass
    spans = _by_name(exporter)
    assert spans["lease.two"].context.trace_id == spans["lease.one"].context.trace_id
    assert spans["lease.two"].parent.span_id == spans["lease.one"].context.span_id
    assert len(spans["lease.two"].links) == 1


def test_non_scalar_attributes_are_stringified_not_dropped_or_raised(exporter: InMemorySpanExporter) -> None:
    with span("x", attributes={"d": {"a": 1}, "n": None, "ok": 3}):
        pass
    attrs = dict(_by_name(exporter)["x"].attributes)
    assert "n" not in attrs and attrs["ok"] == 3 and isinstance(attrs["d"], str)


def test_content_attributes_are_dropped_unless_capture_is_enabled(
    exporter: InMemorySpanExporter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SUBSTRATE_CAPTURE_CONTENT", raising=False)
    with span("llm", attributes={semconv.GEN_AI_INPUT_MESSAGES: "secret prompt", "ok": 1}):
        pass
    assert semconv.GEN_AI_INPUT_MESSAGES not in dict(_by_name(exporter)["llm"].attributes)

    exporter.clear()
    monkeypatch.setenv("SUBSTRATE_CAPTURE_CONTENT", "true")
    with span("llm", attributes={semconv.GEN_AI_INPUT_MESSAGES: "secret prompt"}):
        pass
    assert dict(_by_name(exporter)["llm"].attributes)[semconv.GEN_AI_INPUT_MESSAGES] == "secret prompt"


def test_traceparent_round_trips_and_rejects_garbage() -> None:
    ctx = TraceContext.new()
    assert TraceContext.from_traceparent(ctx.to_traceparent()) == ctx
    for bad in ("", "00-xyz", "01-" + "a" * 32 + "-" + "b" * 16 + "-01", "00-" + "0" * 32 + "-" + "b" * 16 + "-01"):
        with pytest.raises(ValueError):
            TraceContext.from_traceparent(bad)
