"""Conformance suite for ``EventBus``: what every bus (in-memory, Redis) does the same way.

Subclass it and provide the ``bus`` fixture: a connected bus whose streams start empty.
"""

from __future__ import annotations

import asyncio

import pytest

from loamz.engine.agents.kernel.events import (
    EventBus,
    EventEnvelope,
    RunCompleted,
    ThreadCreated,
    parse_event,
)
from loamz.engine.agents.kernel.types.ids import new_id


async def _take(bus: EventBus, event_type: str, group: str, n: int) -> list[EventEnvelope]:
    got: list[EventEnvelope] = []

    async def consume() -> None:
        async for envelope in bus.subscribe(event_type, group, block_ms=100):
            got.append(envelope)
            if len(got) == n:
                return

    await asyncio.wait_for(consume(), timeout=10)
    return got


class EventBusConformance:
    @pytest.fixture
    async def bus(self) -> EventBus:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    @staticmethod
    def _type(name: str) -> str:
        """A stream of its own per test, so a shared server (Redis) never mixes two tests' events."""
        return f"test.{name}.{new_id()}"

    async def test_a_published_event_reaches_a_new_group_from_the_beginning(
        self, bus: EventBus
    ) -> None:
        kind = self._type("first")
        sent = EventEnvelope(event_type=kind, payload={"n": 1})
        await bus.publish(sent)
        [got] = await _take(bus, kind, "g1", 1)
        assert got.event_id == sent.event_id and got.payload == {"n": 1}

    async def test_each_group_sees_every_event_once_in_order(self, bus: EventBus) -> None:
        kind = self._type("groups")
        await bus.publish_many([EventEnvelope(event_type=kind, payload={"n": i}) for i in range(3)])
        for group in ("a", "b"):
            got = await _take(bus, kind, group, 3)
            assert [e.payload["n"] for e in got] == [0, 1, 2]

    async def test_a_consumer_only_receives_its_own_event_type(self, bus: EventBus) -> None:
        mine, other = self._type("mine"), self._type("other")
        await bus.publish(EventEnvelope(event_type=other, payload={"x": 1}))
        await bus.publish(EventEnvelope(event_type=mine, payload={"x": 2}))
        [got] = await _take(bus, mine, "g", 1)
        assert got.payload == {"x": 2}

    async def test_catalog_events_round_trip_through_the_bus(self, bus: EventBus) -> None:
        event = RunCompleted(run_id="r", thread_id="t", tenant_id="acme", user_id="ann", steps=2)
        envelope = event.envelope()
        stream = envelope.model_copy(update={"event_type": self._type("catalog")})
        await bus.publish(stream)
        [got] = await _take(bus, stream.event_type, "g", 1)
        assert parse_event(got.model_copy(update={"event_type": event.event_type})) == event

    async def test_publish_returns_an_id_per_event(self, bus: EventBus) -> None:
        kind = self._type("ids")
        ids = await bus.publish_many(
            [
                ThreadCreated(thread_id=str(i), tenant_id="t", user_id="u")
                .envelope()
                .model_copy(update={"event_type": kind})
                for i in range(2)
            ]
        )
        assert len(ids) == 2 and len(set(ids)) == 2


__all__ = ["EventBusConformance"]
