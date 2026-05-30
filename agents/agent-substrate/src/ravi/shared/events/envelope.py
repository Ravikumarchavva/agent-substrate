"""Standard event envelope for the domain event backbone.

All inter-service async events published to the Redis Streams backbone use
this shape on the wire.  Previously inherited from ``ravi.kernel.contracts._event``
(now deleted); rewritten as a standalone Pydantic model.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    """Domain event envelope with a JSON-shaped payload.

    Fields
    ------
    event_type      : Dotted domain event name, e.g. ``"identity.user_created"``.
    payload         : Event-specific data as a JSON-serializable dict.
    event_id        : Auto-generated UUID for deduplication / idempotency.
    trace_context   : Optional W3C traceparent / tracestate for distributed tracing.
    identity_context: Legacy per-service identity sidecar (kept for compat).
    """

    event_type: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    event_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    trace_context: Optional[Dict[str, str]] = None
    identity_context: Optional[Dict[str, str]] = None

    model_config = {"populate_by_name": True}

    def stream_key(self) -> str:
        """Redis Stream key for this event type: ``events:<event_type>``."""
        return f"events:{self.event_type}"
