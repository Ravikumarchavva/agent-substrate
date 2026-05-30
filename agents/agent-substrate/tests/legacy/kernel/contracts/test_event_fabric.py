"""Tests for kernel event fabric contracts re-exported from ravi.kernel.contracts."""

from __future__ import annotations

from typing import Protocol

from ravi.kernel.contracts import (
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


class TestProtocols:
    def test_durable_event_log_is_protocol(self) -> None:
        assert issubclass(DurableEventLog, Protocol)  # type: ignore[misc]

    def test_realtime_fanout_is_protocol(self) -> None:
        assert issubclass(RealtimeFanout, Protocol)  # type: ignore[misc]

    def test_event_fabric_is_protocol(self) -> None:
        assert issubclass(EventFabric, Protocol)  # type: ignore[misc]

    def test_durable_event_log_runtime_checkable(self) -> None:
        class _Stub:
            async def publish(self, request: PublishRequest, payload: dict) -> str: ...
            async def consume(self, request: ConsumeRequest): ...
            async def ack(self, request: AckRequest) -> None: ...
            async def replay_from(self, topic, partition_key, from_offset, max_messages=100): ...

        assert isinstance(_Stub(), DurableEventLog)

    def test_realtime_fanout_runtime_checkable(self) -> None:
        class _Stub:
            async def publish(self, request: PublishRequest, payload: dict) -> None: ...
            async def subscribe(self, request: SubscribeRequest): ...
            async def unsubscribe(self, subscriber_id: str) -> None: ...

        assert isinstance(_Stub(), RealtimeFanout)

    def test_event_fabric_runtime_checkable(self) -> None:
        class _Stub:
            @property
            def log(self) -> DurableEventLog: ...  # type: ignore[return]
            @property
            def fanout(self) -> RealtimeFanout: ...  # type: ignore[return]
            async def emit(self, request: PublishRequest, payload: dict): ...

        assert isinstance(_Stub(), EventFabric)

    def test_new_types_exported_from_contracts(self) -> None:
        assert EventDeliveryMode is not None
        assert EventPriority is not None
        assert PublishRequest is not None
        assert ConsumeRequest is not None
        assert AckRequest is not None
        assert SubscribeRequest is not None
