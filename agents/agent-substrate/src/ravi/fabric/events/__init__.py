"""In-process reference implementations of the kernel event fabric.

Production deployments swap these out for Redis Streams + Pub/Sub, NATS,
Kafka, etc. — all conform to the same kernel Protocols.
"""

from __future__ import annotations

from ravi.fabric.events._in_memory import (
    InMemoryDurableLog,
    InMemoryEventFabric,
    InMemoryRealtimeFanout,
)

__all__ = [
    "InMemoryDurableLog",
    "InMemoryEventFabric",
    "InMemoryRealtimeFanout",
]
