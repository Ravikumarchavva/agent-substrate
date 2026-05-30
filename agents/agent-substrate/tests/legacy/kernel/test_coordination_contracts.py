from __future__ import annotations

from ravi.kernel.contracts import EventEnvelope, LocalityHint, TemporalSemantics
from ravi.shared.events.envelope import EventEnvelope as SharedEventEnvelope


class TestTypedEventEnvelopeCoordination:
    def test_temporal_event_time_is_none_by_default(self) -> None:
        event = EventEnvelope[dict[str, str]](
            event_type="agent.activated",
            payload={"agent_id": "a/1"},
        )

        assert event.temporal.event_time is None
        assert event.locality.partition_key is None


class TestSharedEventEnvelopeCoordination:
    def test_explicit_temporal_and_locality_are_preserved(self) -> None:
        event = SharedEventEnvelope(
            event_type="thread.updated",
            payload={"thread_id": "t-1"},
            temporal=TemporalSemantics(logical_time=7),
            locality=LocalityHint(
                partition_key="tenant:demo",
                affinity_key="thread:t-1",
                region="us-east-1",
                placement_scope="tenant",
            ),
        )

        dumped = event.model_dump()

        assert event.temporal.logical_time == 7
        assert event.temporal.event_time is None
        assert event.locality.partition_key == "tenant:demo"
        assert dumped["locality"]["region"] == "us-east-1"