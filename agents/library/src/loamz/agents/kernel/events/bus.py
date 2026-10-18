"""The event bus port, and the in-memory bus a single process (or a test) runs on.

Semantics every adapter keeps (``loamz.agents.testing.conformance.EventBusConformance``):

* ``publish`` appends to the event type's stream and returns the event's position in it;
* ``subscribe(event_type, group)`` delivers each event of that type once to the group (several consumers of one group
  share the work; each group sees every event), starting from the beginning of the stream when the group is new.
"""

from __future__ import annotations

import asyncio
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


class InMemoryEventBus:
    """An ``EventBus`` inside one process: streams are lists, consumer groups are cursors."""

    def __init__(self) -> None:
        self._streams: dict[str, list[EventEnvelope]] = {}
        self._cursors: dict[tuple[str, str], int] = {}
        self._changed = asyncio.Condition()

    async def connect(self) -> None:
        return None

    async def disconnect(self) -> None:
        return None

    async def publish(self, event: EventEnvelope) -> str:
        async with self._changed:
            stream = self._streams.setdefault(event.event_type, [])
            stream.append(event)
            self._changed.notify_all()
            return f"{len(stream)}-0"

    async def publish_many(self, events: list[EventEnvelope]) -> list[str]:
        return [await self.publish(event) for event in events]

    async def subscribe(
        self, event_type: str, group: str, batch_size: int = 10, block_ms: int = 1000
    ) -> AsyncIterator[EventEnvelope]:
        key = (event_type, group)
        self._cursors.setdefault(key, 0)
        while True:
            async with self._changed:
                await self._changed.wait_for(
                    lambda: self._cursors[key] < len(self._streams.get(event_type, []))
                )
                event = self._streams[event_type][self._cursors[key]]
                self._cursors[key] += 1
            yield event


__all__ = ["EventBus", "InMemoryEventBus"]
