"""The in-memory ``EventBus``: one process (a laptop, a test) needs no broker."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from loamz.engine.agents.kernel.events.envelope import EventEnvelope


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


__all__ = ["InMemoryEventBus"]
