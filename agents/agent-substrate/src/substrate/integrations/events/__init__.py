"""Redis-backed event bus integration.

``RedisPubSubFanout``, ``RedisStreamsDurableLog``, and ``RedisLeaseRegistry``
depended on ``substrate.kernel.events._fabric`` which was removed during the
kernel/fabric migration. They are parked until a replacement fabric Protocol
is defined.
"""

from __future__ import annotations


from substrate.integrations.events.envelope import EventEnvelope
from substrate.integrations.events.redis_event_bus import EventBus

__all__ = ["EventBus", "EventEnvelope"]
