"""Envelope span contracts for observability backends.

The kernel only defines the shape of a span and the Protocol a recorder must
implement. Concrete exporters, OpenTelemetry bridges, and in-memory stores
belong in extension or integration layers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum, auto
from typing import Protocol, runtime_checkable

from ravi.kernel.runtime._message import Envelope

__all__ = [
    "EnvelopeSpan",
    "EnvelopeSpanRecorder",
    "SpanQuery",
    "SpanStatus",
]


UTC = timezone.utc
SpanAttributes = tuple[tuple[str, str], ...]


def _utc_now() -> datetime:
    return datetime.now(UTC)


class SpanStatus(Enum):
    """Lifecycle state of a span tied to a single envelope."""

    STARTED = auto()
    OK = auto()
    FAILED = auto()
    CANCELLED = auto()


@dataclass(frozen=True, slots=True)
class EnvelopeSpan:
    """One observability span for one runtime envelope.

    ``envelope_id`` is the envelope's ``correlation_id``. ``correlation_id``
    lets operators follow a logical request across multiple envelopes, while
    ``trace_id`` links this neutral representation to external trace systems.
    """

    envelope_id: str
    correlation_id: str
    name: str = "envelope"
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    trace_id: str | None = None
    causation_id: str | None = None
    parent_span_id: str | None = None
    tenant_id: str = "default"
    workspace_id: str = "default"
    actor_id: str = ""
    event_type: str = ""
    sender: str | None = None
    target: str | None = None
    started_at: datetime = field(default_factory=_utc_now)
    ended_at: datetime | None = None
    status: SpanStatus = SpanStatus.STARTED
    attributes: SpanAttributes = ()

    @classmethod
    def from_envelope(
        cls,
        envelope: Envelope,
        *,
        name: str = "envelope",
        parent_span_id: str | None = None,
        attributes: SpanAttributes = (),
    ) -> EnvelopeSpan:
        """Create a span snapshot from an :class:`Envelope`.

        Tenancy and actor identity are derived from ``envelope.identity`` when
        present — the in-process envelope no longer carries denormalised
        tenant/workspace/actor fields.
        """
        identity = envelope.identity
        return cls(
            envelope_id=envelope.correlation_id,
            correlation_id=envelope.correlation_id,
            name=name,
            trace_id=envelope.trace_id,
            causation_id=envelope.causation_id,
            parent_span_id=parent_span_id,
            tenant_id=identity.effective_tenant_id if identity is not None else "default",
            workspace_id=identity.effective_workspace_id if identity is not None else "default",
            actor_id=identity.principal.fqn if identity is not None else "",
            sender=str(envelope.sender) if envelope.sender is not None else None,
            target=str(envelope.target) if envelope.target is not None else None,
            attributes=attributes,
        )

    @property
    def is_finished(self) -> bool:
        """True when the span has a terminal status and end timestamp."""

        return self.ended_at is not None

    @property
    def duration_ms(self) -> float | None:
        """Elapsed milliseconds for a finished span."""

        if self.ended_at is None:
            return None
        return (self.ended_at - self.started_at).total_seconds() * 1000.0

    def finish(
        self,
        *,
        status: SpanStatus = SpanStatus.OK,
        ended_at: datetime | None = None,
        attributes: SpanAttributes = (),
    ) -> EnvelopeSpan:
        """Return a finished copy of this span."""

        if status is SpanStatus.STARTED:
            raise ValueError("finished spans cannot keep STARTED status")
        return replace(
            self,
            ended_at=ended_at or _utc_now(),
            status=status,
            attributes=(*self.attributes, *attributes),
        )


@dataclass(frozen=True, slots=True)
class SpanQuery:
    """Filter for querying recorded envelope spans."""

    envelope_id: str | None = None
    correlation_id: str | None = None
    trace_id: str | None = None
    tenant_id: str | None = None
    workspace_id: str | None = None
    status: SpanStatus | None = None
    limit: int = 100

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError(f"limit must be > 0, got {self.limit!r}")


@runtime_checkable
class EnvelopeSpanRecorder(Protocol):
    """Storage/query contract for envelope-level observability spans."""

    async def start_span(self, span: EnvelopeSpan) -> EnvelopeSpan:
        """Record a newly-started span and return the persisted value."""
        ...

    async def finish_span(
        self,
        span_id: str,
        *,
        status: SpanStatus = SpanStatus.OK,
        ended_at: datetime | None = None,
        attributes: SpanAttributes = (),
    ) -> EnvelopeSpan:
        """Mark ``span_id`` as finished and return the final span."""
        ...

    async def span_for(self, span_id: str) -> EnvelopeSpan | None:
        """Return one span by id, or ``None`` when absent."""
        ...

    async def spans_for_envelope(self, envelope_id: str) -> tuple[EnvelopeSpan, ...]:
        """Return all spans recorded for one envelope."""
        ...

    async def spans_for_correlation(
        self, correlation_id: str
    ) -> tuple[EnvelopeSpan, ...]:
        """Return all spans recorded for one correlation id."""
        ...

    async def query_spans(self, query: SpanQuery) -> tuple[EnvelopeSpan, ...]:
        """Return spans matching ``query``."""
        ...
