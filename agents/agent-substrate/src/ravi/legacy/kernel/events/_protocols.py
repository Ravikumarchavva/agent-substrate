"""Split event fabric contracts — durable log vs realtime fanout.

Two fundamentally different delivery semantics:

- ``DurableEventLog`` — ordered, replayable, partitioned; at-least-once;
  backed by Kafka, Redis Streams, or equivalent.
- ``RealtimeFanout`` — low-latency ephemeral propagation; best-effort;
  backed by NATS, Redis pub/sub, or equivalent.
- ``EventFabric`` — unified entry point that routes to one or both.

Implementations live in the integration / extension layers.
This module contains only Protocol contracts and the pure-Python
value types that accompany them.

No imports from shared, extensions, integrations, catalog, server,
services, configs, or logger are permitted here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, AsyncIterator, Protocol, runtime_checkable

__all__ = [
    "EventDeliveryMode",
    "EventPriority",
    "PublishRequest",
    "ConsumeRequest",
    "AckRequest",
    "SubscribeRequest",
    "DurableEventLog",
    "RealtimeFanout",
    "EventFabric",
]


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class EventDeliveryMode(Enum):
    """How the fabric should deliver this event."""

    DURABLE_LOG = auto()
    """Write to ordered durable log; at-least-once delivery; replayable."""
    REALTIME_FANOUT = auto()
    """Ephemeral pub/sub; best-effort; low-latency; NOT replayable."""
    BOTH = auto()
    """Write to log AND fan out immediately (default for most domain events)."""


class EventPriority(Enum):
    LOW = auto()
    NORMAL = auto()
    HIGH = auto()
    CRITICAL = auto()


# ---------------------------------------------------------------------------
# Request value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PublishRequest:
    """What the caller asks the fabric to do with an event.

    Decoupled from the event payload itself.
    """

    topic: str
    """Logical topic name."""
    partition_key: str
    """Controls log shard placement."""
    delivery_mode: EventDeliveryMode = EventDeliveryMode.BOTH
    priority: EventPriority = EventPriority.NORMAL
    dedup_key: str | None = None
    """Idempotency key — fabric discards duplicates."""
    max_delivery_attempts: int = 3
    drop_on_full: bool = False
    """Backpressure hint: drop on overflow vs block."""


@dataclass(frozen=True, slots=True)
class ConsumeRequest:
    """What the caller asks when pulling from the durable log."""

    topic: str
    partition_key: str
    consumer_group: str
    consumer_id: str
    max_messages: int = 10
    block_ms: int = 0
    """0 = non-blocking; >0 = block up to N ms."""


@dataclass(frozen=True, slots=True)
class AckRequest:
    """Acknowledge delivery of a durable log message."""

    topic: str
    consumer_group: str
    message_id: str


@dataclass(frozen=True, slots=True)
class SubscribeRequest:
    """Subscribe to realtime fanout for a topic pattern."""

    topic_pattern: str
    """Exact match or glob (e.g. ``"agent.*/completed"``)."""
    subscriber_id: str
    max_queue_depth: int = 1000
    """Drop oldest when exceeded."""


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class DurableEventLog(Protocol):
    """Contract for the durable, ordered, replayable event log substrate.

    Backed by Kafka, Redis Streams, or equivalent in production.
    """

    async def publish(self, request: PublishRequest, payload: dict[str, Any]) -> str:
        """Append payload to the log. Returns the assigned message_id."""
        ...

    async def consume(
        self, request: ConsumeRequest
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield ``(message_id, payload)`` tuples from the log."""
        ...

    async def ack(self, request: AckRequest) -> None:
        """Acknowledge a message so it is not redelivered."""
        ...

    async def replay_from(
        self,
        topic: str,
        partition_key: str,
        from_offset: str,
        max_messages: int = 100,
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Replay historical messages from a given offset."""
        ...


@runtime_checkable
class RealtimeFanout(Protocol):
    """Contract for ephemeral, low-latency, best-effort realtime propagation.

    Backed by NATS, Redis pub/sub, or equivalent in production.
    """

    async def publish(self, request: PublishRequest, payload: dict[str, Any]) -> None:
        """Fan out to all matching subscribers. Best-effort, no persistence."""
        ...

    async def subscribe(
        self, request: SubscribeRequest
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Yield ``(topic, payload)`` tuples as they arrive. Ephemeral."""
        ...

    async def unsubscribe(self, subscriber_id: str) -> None:
        """Cancel all subscriptions for a subscriber."""
        ...


@runtime_checkable
class EventFabric(Protocol):
    """Unified entry point routing to durable log and/or realtime fanout.

    Routes based on ``PublishRequest.delivery_mode``.
    """

    @property
    def log(self) -> DurableEventLog: ...

    @property
    def fanout(self) -> RealtimeFanout: ...

    async def emit(
        self, request: PublishRequest, payload: dict[str, Any]
    ) -> str | None:
        """Route based on delivery_mode.

        - ``DURABLE_LOG``    → ``log.publish()`` → returns message_id
        - ``REALTIME_FANOUT`` → ``fanout.publish()`` → returns ``None``
        - ``BOTH``           → both, returns log message_id
        """
        ...
