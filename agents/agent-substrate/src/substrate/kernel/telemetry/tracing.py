"""Spans, done so that they nest and keep their outcome.

The tracing this replaces had two defects that follow from how a span object is
used, and both are removed by construction here rather than by care:

* It started spans without making them *current*, so every span was its own root
  and one agent turn produced four disconnected traces. ``span()`` always makes
  the span current for the duration of the block, so anything started inside is
  its child.
* It set outcome attributes after the span had ended, where OpenTelemetry drops
  them. ``span()`` yields a handle that is only valid while the span is open;
  the outcome is recorded inside the block, before it closes.

Control-flow signals (suspension, cancellation, a lost lease) are not errors: a
run that goes dormant did not fail, so they unwind through a span without marking
it failed.

Only ``opentelemetry-api`` is imported. Until an application configures an SDK it
is a no-op, so the engine instruments itself unconditionally and costs nothing
when nobody is listening.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import (
    Link,
    NonRecordingSpan,
    Span,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    TraceFlags,
)

from substrate.kernel.abstractions.core.trace import TraceContext
from substrate.kernel.abstractions.exceptions import ControlSignal, KernelError
from substrate.kernel.telemetry import semconv

_TRACER_NAME = "substrate.kernel"

AttributeValue = str | int | float | bool


def capture_content() -> bool:
    """Whether prompt/response/tool text may be put on spans.

    Off by default, and read per call so it can be switched without a restart.
    """
    return os.environ.get("SUBSTRATE_CAPTURE_CONTENT", "").lower() in ("1", "true", "yes")


def _clean(attributes: Mapping[str, Any] | None) -> dict[str, AttributeValue]:
    """Keep only scalar attributes, and drop content unless capture is enabled."""
    if not attributes:
        return {}
    allow_content = capture_content()
    cleaned: dict[str, AttributeValue] = {}
    for key, value in attributes.items():
        if value is None:
            continue
        if key in semconv.CONTENT_ATTRIBUTES and not allow_content:
            continue
        if isinstance(value, (str, int, float, bool)):
            cleaned[key] = value
        elif isinstance(value, (list, tuple)) and all(isinstance(v, (str, int, float, bool)) for v in value):
            cleaned[key] = list(value)  # type: ignore[assignment]
        else:
            cleaned[key] = str(value)
    return cleaned


def _span_context(context: TraceContext) -> SpanContext:
    return SpanContext(
        trace_id=int(context.trace_id, 16),
        span_id=int(context.span_id, 16),
        is_remote=True,
        trace_flags=TraceFlags(TraceFlags.SAMPLED if context.sampled else TraceFlags.DEFAULT),
    )


def _remote_context(context: TraceContext) -> Any:
    return trace.set_span_in_context(NonRecordingSpan(_span_context(context)))


class SpanHandle:
    """A span that is open. Not usable after the ``with`` block that made it."""

    def __init__(self, span: Span) -> None:
        self._span = span

    def set(self, **attributes: Any) -> None:
        """Record attributes on the open span (keys as ``semconv`` constants via
        ``set_attribute`` for dotted names)."""
        for key, value in _clean(attributes).items():
            self._span.set_attribute(key, value)

    def set_attribute(self, key: str, value: Any) -> None:
        for k, v in _clean({key: value}).items():
            self._span.set_attribute(k, v)

    def set_attributes(self, attributes: Mapping[str, Any]) -> None:
        for key, value in _clean(attributes).items():
            self._span.set_attribute(key, value)

    def context(self) -> TraceContext | None:
        """This span's position, to persist for work that continues it elsewhere."""
        span_context = self._span.get_span_context()
        if not span_context.is_valid:
            return None
        return TraceContext(
            trace_id=f"{span_context.trace_id:032x}",
            span_id=f"{span_context.span_id:016x}",
            sampled=bool(span_context.trace_flags & TraceFlags.SAMPLED),
        )

    def error(self, exc: BaseException) -> None:
        self._span.record_exception(exc)
        self._span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
        if isinstance(exc, KernelError):
            self._span.set_attribute(semconv.ERROR_CODE, exc.code)
            self._span.set_attribute(semconv.ERROR_RETRYABLE, exc.retryable)


@contextmanager
def span(
    name: str,
    *,
    attributes: Mapping[str, Any] | None = None,
    parent: TraceContext | None = None,
    links: Sequence[TraceContext] = (),
    kind: SpanKind = SpanKind.INTERNAL,
) -> Iterator[SpanHandle]:
    """Open a span that is current for the block, nested under whatever is current.

    ``parent`` continues a trace from persisted data (a resumed lease, a spawned
    child) instead of the in-process current span. ``links`` relate this span to
    others without making it their child — a resumed lease links back to the
    attempt before it.
    """
    tracer = trace.get_tracer(_TRACER_NAME)
    otel_links = [Link(_span_context(link)) for link in links]
    context = _remote_context(parent) if parent is not None else None
    with tracer.start_as_current_span(
        name,
        context=context,
        kind=kind,
        attributes=_clean(attributes),
        links=otel_links,
        record_exception=False,
        set_status_on_exception=False,
    ) as otel_span:
        handle = SpanHandle(otel_span)
        try:
            yield handle
        except ControlSignal as signal:
            # Suspension, cancellation and a lost lease are not failures.
            otel_span.set_attribute(semconv.SUSPENDED, True)
            otel_span.set_attribute("substrate.signal", type(signal).__name__)
            raise
        except BaseException as exc:
            handle.error(exc)
            raise


def current_trace_context() -> TraceContext | None:
    """The trace position of whatever span is current, if any."""
    span_context = trace.get_current_span().get_span_context()
    if not span_context.is_valid:
        return None
    return TraceContext(
        trace_id=f"{span_context.trace_id:032x}",
        span_id=f"{span_context.span_id:016x}",
        sampled=bool(span_context.trace_flags & TraceFlags.SAMPLED),
    )


__all__ = ["SpanHandle", "capture_content", "current_trace_context", "span"]
