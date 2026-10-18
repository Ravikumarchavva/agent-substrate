"""The event bus port.

Semantics every adapter keeps (``loamz.agents.testing.conformance.EventBusConformance``):

* ``publish`` appends to the event type's stream and returns the event's position in it;
* ``subscribe(event_type, group)`` delivers each event of that type once to the group (several consumers of one group
  share the work; each group sees every event), starting from the beginning of the stream when the group is new.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from loamz.agents.kernel.events.envelope import EventEnvelope


@runtime_checkable
class EventBus(Protocol):
    async def connect(self) -> None: ...

    async def disconnect(self) -> None: ...

    async def publish(self, event: EventEnvelope) -> str: ...

    async def publish_many(self, events: list[EventEnvelope]) -> list[str]: ...

    def subscribe(
        self, event_type: str, group: str, batch_size: int = 10, block_ms: int = 1000
    ) -> AsyncIterator[EventEnvelope]: ...


__all__ = ["EventBus"]
