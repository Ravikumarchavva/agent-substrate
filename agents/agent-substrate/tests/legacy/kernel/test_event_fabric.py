"""Tests for the new kernel event fabric contracts in ravi.kernel.events._fabric."""

from __future__ import annotations


import pytest

from ravi.kernel.events._fabric import (
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
from ravi.kernel import EventFabric as KernelEventFabric


# ---------------------------------------------------------------------------
# 1. EventDeliveryMode members
# ---------------------------------------------------------------------------


def test_event_delivery_mode_members() -> None:
    assert EventDeliveryMode.DURABLE_LOG
    assert EventDeliveryMode.REALTIME_FANOUT
    assert EventDeliveryMode.BOTH
    assert {m.name for m in EventDeliveryMode} == {
        "DURABLE_LOG",
        "REALTIME_FANOUT",
        "BOTH",
    }


# ---------------------------------------------------------------------------
# 2. EventPriority members
# ---------------------------------------------------------------------------


def test_event_priority_members() -> None:
    assert {m.name for m in EventPriority} == {"LOW", "NORMAL", "HIGH", "CRITICAL"}


# ---------------------------------------------------------------------------
# 3. PublishRequest defaults
# ---------------------------------------------------------------------------


def test_publish_request_defaults() -> None:
    req = PublishRequest(topic="t", partition_key="pk")
    assert req.delivery_mode is EventDeliveryMode.BOTH
    assert req.priority is EventPriority.NORMAL
    assert req.dedup_key is None
    assert req.max_delivery_attempts == 3
    assert req.drop_on_full is False


def test_publish_request_frozen() -> None:
    req = PublishRequest(topic="t", partition_key="pk")
    with pytest.raises((AttributeError, TypeError)):
        req.topic = "mutated"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 4. ConsumeRequest defaults
# ---------------------------------------------------------------------------


def test_consume_request_defaults() -> None:
    req = ConsumeRequest(
        topic="t",
        partition_key="pk",
        consumer_group="cg",
        consumer_id="c1",
    )
    assert req.max_messages == 10
    assert req.block_ms == 0


def test_consume_request_frozen() -> None:
    req = ConsumeRequest(topic="t", partition_key="pk", consumer_group="cg", consumer_id="c1")
    with pytest.raises((AttributeError, TypeError)):
        req.max_messages = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 5. SubscribeRequest defaults
# ---------------------------------------------------------------------------


def test_subscribe_request_defaults() -> None:
    req = SubscribeRequest(topic_pattern="agent.*", subscriber_id="s1")
    assert req.max_queue_depth == 1000


def test_subscribe_request_frozen() -> None:
    req = SubscribeRequest(topic_pattern="agent.*", subscriber_id="s1")
    with pytest.raises((AttributeError, TypeError)):
        req.max_queue_depth = 5  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 6. DurableEventLog isinstance check
# ---------------------------------------------------------------------------


def test_durable_event_log_isinstance() -> None:
    class _Impl:
        async def publish(self, request: PublishRequest, payload: dict) -> str: ...
        async def consume(self, request: ConsumeRequest): ...
        async def ack(self, request: AckRequest) -> None: ...
        async def replay_from(self, topic, partition_key, from_offset, max_messages=100): ...

    assert isinstance(_Impl(), DurableEventLog)


def test_durable_event_log_missing_method_fails() -> None:
    class _Incomplete:
        async def publish(self, request: PublishRequest, payload: dict) -> str: ...
        # missing consume, ack, replay_from

    assert not isinstance(_Incomplete(), DurableEventLog)


# ---------------------------------------------------------------------------
# 7. RealtimeFanout isinstance check
# ---------------------------------------------------------------------------


def test_realtime_fanout_isinstance() -> None:
    class _Impl:
        async def publish(self, request: PublishRequest, payload: dict) -> None: ...
        async def subscribe(self, request: SubscribeRequest): ...
        async def unsubscribe(self, subscriber_id: str) -> None: ...

    assert isinstance(_Impl(), RealtimeFanout)


def test_realtime_fanout_missing_method_fails() -> None:
    class _Incomplete:
        async def publish(self, request: PublishRequest, payload: dict) -> None: ...
        # missing subscribe, unsubscribe

    assert not isinstance(_Incomplete(), RealtimeFanout)


# ---------------------------------------------------------------------------
# 8. EventFabric isinstance check
# ---------------------------------------------------------------------------


def test_event_fabric_isinstance() -> None:
    class _Impl:
        @property
        def log(self) -> DurableEventLog: ...  # type: ignore[return]
        @property
        def fanout(self) -> RealtimeFanout: ...  # type: ignore[return]
        async def emit(self, request: PublishRequest, payload: dict): ...

    assert isinstance(_Impl(), EventFabric)


def test_event_fabric_missing_property_fails() -> None:
    class _Incomplete:
        # missing log property
        @property
        def fanout(self) -> RealtimeFanout: ...  # type: ignore[return]
        async def emit(self, request: PublishRequest, payload: dict): ...

    assert not isinstance(_Incomplete(), EventFabric)


# ---------------------------------------------------------------------------
# 9. Import from ravi.kernel works
# ---------------------------------------------------------------------------


def test_event_fabric_importable_from_ravi_kernel() -> None:
    assert KernelEventFabric is EventFabric
