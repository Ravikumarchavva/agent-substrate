"""Versioned Event envelope — shared by in-process and distributed event buses.

Both the kernel pub/sub (``AgentRuntime.publish_message``) and the
infrastructure event bus (``serving/shared/events/``) carry ``Event``
objects so there is one event contract across transports (Redis, NATS,
in-process asyncio).

``EventPublisher`` and ``EventSubscriber`` protocols abstract the transport:
in-process, Redis pub/sub, NATS, Kafka, etc. can all implement them.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncIterator, Awaitable, Callable, Protocol

from pydantic import BaseModel, Field

from ravi.kernel.content import JsonObject
from ravi.kernel.identity import AgentId


class Event(BaseModel):
    """Versioned event envelope carrying any JSON-serializable payload.

    ``id`` — unique event identifier; enables deduplication at consumers.
    ``type`` — event type string (e.g. ``"agent.started"``, ``"tool.called"``).
    ``source`` — emitting agent id as string (``str(AgentId(...))``) or a
                 service name for infrastructure events.
    ``schema_version`` — monotonically increasing integer bumped when the
                         ``data`` schema changes; consumers can branch on it.
    ``correlation_id`` — ties all events in one logical conversation/run.
    ``ts`` — emission wall-clock time (UTC).
    ``data`` — event-specific payload; consumers type-narrow on ``type``.
    """

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: str
    source: str
    schema_version: int = 1
    correlation_id: str = ""
    ts: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    data: JsonObject = Field(default_factory=dict)

    model_config = {"frozen": True}

    @classmethod
    def create(
        cls,
        event_type: str,
        *,
        source: AgentId | str,
        data: JsonObject | None = None,
        correlation_id: str = "",
        schema_version: int = 1,
    ) -> "Event":
        """Convenience constructor — ``source`` accepts ``AgentId`` or string."""
        return cls(
            type=event_type,
            source=str(source),
            data=data or {},
            correlation_id=correlation_id,
            schema_version=schema_version,
        )


EventHandler = Callable[[Event], Awaitable[None]]
"""Type alias for an async event handler callback."""


class EventPublisher(Protocol):
    """Contract for publishing events to a transport."""

    async def publish(self, event: Event, *, topic: str = "") -> None: ...


class EventSubscriber(Protocol):
    """Contract for subscribing to events from a transport."""

    async def subscribe(
        self,
        topic: str,
        handler: EventHandler,
    ) -> str:
        """Subscribe *handler* to *topic*. Returns a subscription id."""
        ...

    async def unsubscribe(self, subscription_id: str) -> None: ...

    def stream(self, topic: str) -> AsyncIterator[Event]:
        """Return an async iterator of events on *topic*."""
        ...


__all__ = ["Event", "EventHandler", "EventPublisher", "EventSubscriber"]
