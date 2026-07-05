"""Standard event envelope for the domain event backbone.

All inter-service async events published to the Redis Streams backbone use
this shape on the wire.  Previously inherited from ``substrate.kernel.contracts._event``
(now deleted); rewritten as a standalone Pydantic model.
"""

from __future__ import annotations

import uuid
from typing import Any

from substrate.kernel import Event
from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    """Domain event envelope with a JSON-shaped payload.

    Fields
    ------
    event_type    : Dotted domain event name, e.g. ``"identity.user_created"``.
    payload       : Event-specific data as a JSON-serializable dict.
    event_id      : Auto-generated UUID for deduplication / idempotency.
    trace_context : Optional W3C traceparent / tracestate for distributed tracing.
    """

    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    trace_context: dict[str, str] | None = None

    model_config = {"populate_by_name": True}

    def stream_key(self) -> str:
        """Redis Stream key for this event type: ``events:<event_type>``."""
        return f"events:{self.event_type}"

    def to_kernel_event(self) -> "Event":
        """Convert this serving event envelope to a kernel event."""
        return Event(
            id=self.event_id,
            type=self.event_type,
            source="serving",
            data={
                **self.payload,
                **({"_trace": self.trace_context} if self.trace_context else {}),
            },
        )

    @classmethod
    def from_kernel_event(cls, event: "Event") -> "EventEnvelope":
        """Convert a kernel event to this serving event envelope."""
        return cls(
            event_type=event.type,
            payload=dict(event.data),
            event_id=event.id,
        )
