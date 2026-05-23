"""Generic typed event envelope — canonical domain event contract.

The existing ``shared.events.envelope.EventEnvelope`` has an untyped
``payload: Dict[str, Any]``. This replaces it with a proper Generic so
event producers and consumers share a typed contract.

Migration path (Sprint 6): switch all ``shared.events.types`` factories
and all event bus consumers to import from here. Then delete the untyped
version in ``shared/events/envelope.py``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class EventEnvelope(BaseModel, Generic[T]):
    """Typed domain event envelope.

    ``T`` is the payload type.  Use ``dict[str, Any]`` for unstructured
    legacy events; use a specific Pydantic model for fully-typed domain
    events (recommended for all new event types).

    All inter-service async events are wrapped in this envelope before
    being published to the Redis Streams backbone.

    Tracing:
        ``correlation_id`` groups request/response pairs across services.
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    event_version: int = 1
    emitted_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    tenant_id: str = "default"
    workspace_id: str = "default"
    actor_id: str = ""
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    payload: T

    model_config = {"arbitrary_types_allowed": True}

    def stream_key(self) -> str:
        """Redis Stream key for routing this event."""
        return f"events:{self.event_type}"
