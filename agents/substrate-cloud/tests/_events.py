"""An in-memory event bus with the interface of the Redis one (``publish`` and ``subscribe``), for tests of the services."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator

from substrate.integrations.events.envelope import EventEnvelope


class MemoryEventBus:
    def __init__(self) -> None:
        self.log: dict[str, list[EventEnvelope]] = defaultdict(list)
        self._changed = asyncio.Condition()

    async def connect(self) -> None:  # the Redis bus connects; this one is always ready
        return None

    async def disconnect(self) -> None:
        return None

    async def publish(self, event: EventEnvelope) -> str:
        self.log[event.event_type].append(event)
        async with self._changed:
            self._changed.notify_all()
        return event.event_id

    async def subscribe(
        self, event_type: str, group: str, **_: object
    ) -> AsyncIterator[EventEnvelope]:
        """Every event of the type from the start (a consumer group begins at 0), then each new one."""
        position = 0
        while True:
            log = self.log[event_type]
            if position < len(log):
                position += 1
                yield log[position - 1]
                continue
            async with self._changed:
                await self._changed.wait_for(
                    lambda: position < len(self.log[event_type])
                )

    def types(self) -> list[str]:
        return [e.event_type for events in self.log.values() for e in events]
