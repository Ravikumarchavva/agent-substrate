"""Standard event envelope for the domain event backbone.

Every event published to Redis Streams MUST use this envelope format.
Consumer services declare which event_type/event_version combos they handle.

Envelope fields (per docs/microservices/03-data-ownership-and-contract-standards.md):
  event_id, event_type, event_version, emitted_at, tenant_id, workspace_id,
  actor_id, correlation_id, payload
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Dict

from pydantic import BaseModel, Field


class EventEnvelope(BaseModel):
    """Immutable domain event envelope.

    All inter-service async events are wrapped in this envelope before
    being published to the Redis Streams backbone. Payloads must be
    serializable to JSON.
    """

    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str
    event_version: int = 1
    emitted_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    tenant_id: str = "default"
    workspace_id: str = "default"
    actor_id: str = ""
    correlation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    payload: Dict[str, Any] = Field(default_factory=dict)

    def stream_key(self) -> str:
        """Return the Redis Stream key for this event type."""
        return f"events:{self.event_type}"
