"""Standard event envelope for the domain event backbone.

All inter-service async events published to the Redis Streams backbone use
this shape on the wire.
"""

from __future__ import annotations

import uuid
from typing import Any

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
