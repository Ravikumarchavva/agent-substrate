"""ravi.fabric.events — event infrastructure Protocols + implementations + envelope.

Protocols (``_protocols.py``) define the durable-log and realtime-fanout
contracts. In-memory implementations (``_in_memory.py``) satisfy them
without infrastructure for tests and local dev. Production backends
(Redis Streams, NATS) implement the same Protocols.

``EventEnvelope`` (``_envelope.py``) is the canonical cross-service wire format.
"""

from __future__ import annotations

from ravi.fabric.events._protocols import (
    AckRequest,
    ConsumeRequest,
    DurableEventLog,
    EventDeliveryMode,
    EventFabric,
    EventPriority,
    PublishRequest,
    RealtimeFanout,
    SubscribeRequest,
)
from ravi.fabric.events._in_memory import (
    InMemoryDurableLog,
    InMemoryEventFabric,
    InMemoryRealtimeFanout,
)
from ravi.fabric.events._envelope import EventEnvelope

__all__ = [
    # Protocols
    "EventDeliveryMode",
    "EventPriority",
    "PublishRequest",
    "ConsumeRequest",
    "AckRequest",
    "SubscribeRequest",
    "DurableEventLog",
    "RealtimeFanout",
    "EventFabric",
    # In-memory impls
    "InMemoryDurableLog",
    "InMemoryEventFabric",
    "InMemoryRealtimeFanout",
    # Wire format
    "EventEnvelope",
]
