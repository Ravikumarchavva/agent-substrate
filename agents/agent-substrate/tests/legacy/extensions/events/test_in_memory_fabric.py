"""Tests for the in-memory EventFabric reference implementations.

These exist to anchor the kernel Protocols against a real implementation
so any Redis/Kafka/NATS backend that follows the same Protocols is
behaviourally compatible.
"""

from __future__ import annotations

import asyncio


from ravi.fabric.events import (
    InMemoryDurableLog,
    InMemoryEventFabric,
    InMemoryRealtimeFanout,
)
from ravi.kernel.events._fabric import (
    ConsumeRequest,
    DurableEventLog,
    EventDeliveryMode,
    EventFabric,
    PublishRequest,
    RealtimeFanout,
    SubscribeRequest,
)


class TestDurableLog:
    async def test_publish_then_consume_yields_payload(self) -> None:
        log = InMemoryDurableLog()
        msg_id = await log.publish(
            PublishRequest(topic="t", partition_key="p"), {"k": "v"}
        )
        assert isinstance(msg_id, str) and msg_id

        consumed = []
        async for item in log.consume(
            ConsumeRequest(
                topic="t", partition_key="p", consumer_group="g", consumer_id="c"
            )
        ):
            consumed.append(item)
        assert consumed == [(msg_id, {"k": "v"})]

    async def test_two_groups_each_see_every_message(self) -> None:
        log = InMemoryDurableLog()
        msg_id = await log.publish(
            PublishRequest(topic="t", partition_key="p"), {"a": 1}
        )

        seen_g1, seen_g2 = [], []
        async for it in log.consume(
            ConsumeRequest(topic="t", partition_key="p", consumer_group="g1", consumer_id="c")
        ):
            seen_g1.append(it)
        async for it in log.consume(
            ConsumeRequest(topic="t", partition_key="p", consumer_group="g2", consumer_id="c")
        ):
            seen_g2.append(it)
        assert seen_g1 == [(msg_id, {"a": 1})]
        assert seen_g2 == [(msg_id, {"a": 1})]

    async def test_cursor_advances_so_replay_is_explicit(self) -> None:
        log = InMemoryDurableLog()
        a = await log.publish(PublishRequest(topic="t", partition_key="p"), {"i": 0})
        b = await log.publish(PublishRequest(topic="t", partition_key="p"), {"i": 1})

        # First consume: see both
        seen = []
        async for it in log.consume(
            ConsumeRequest(topic="t", partition_key="p", consumer_group="g", consumer_id="c", max_messages=10)
        ):
            seen.append(it)
        assert [s[0] for s in seen] == [a, b]

        # Second consume: cursor advanced, see nothing
        seen2 = []
        async for it in log.consume(
            ConsumeRequest(topic="t", partition_key="p", consumer_group="g", consumer_id="c", max_messages=10)
        ):
            seen2.append(it)
        assert seen2 == []

        # Replay from offset 0: see both again
        replayed = []
        async for it in log.replay_from("t", "p", "0", max_messages=10):
            replayed.append(it)
        assert [r[0] for r in replayed] == [a, b]

    async def test_block_ms_returns_after_deadline_when_empty(self) -> None:
        log = InMemoryDurableLog()
        seen = []
        start = asyncio.get_running_loop().time()
        async for it in log.consume(
            ConsumeRequest(
                topic="t",
                partition_key="p",
                consumer_group="g",
                consumer_id="c",
                block_ms=50,
            )
        ):
            seen.append(it)
        elapsed = asyncio.get_running_loop().time() - start
        assert seen == []
        assert elapsed >= 0.04

    async def test_isinstance_runtime_protocol(self) -> None:
        log = InMemoryDurableLog()
        assert isinstance(log, DurableEventLog)


class TestRealtimeFanout:
    async def test_glob_pattern_match(self) -> None:
        fanout = InMemoryRealtimeFanout()
        received: list[tuple[str, dict]] = []
        done = asyncio.Event()

        async def consume() -> None:
            async for item in fanout.subscribe(
                SubscribeRequest(topic_pattern="agent.*", subscriber_id="s1")
            ):
                received.append(item)
                if len(received) == 2:
                    done.set()
                    return

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)  # let subscribe register

        await fanout.publish(
            PublishRequest(topic="agent.created", partition_key="x"),
            {"i": 1},
        )
        await fanout.publish(
            PublishRequest(topic="other.created", partition_key="x"),
            {"i": 2},  # should NOT match
        )
        await fanout.publish(
            PublishRequest(topic="agent.deleted", partition_key="x"),
            {"i": 3},
        )

        await asyncio.wait_for(done.wait(), timeout=1.0)
        topics = [t for t, _ in received]
        assert topics == ["agent.created", "agent.deleted"]
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_isinstance_runtime_protocol(self) -> None:
        fanout = InMemoryRealtimeFanout()
        assert isinstance(fanout, RealtimeFanout)


class TestEventFabricRouting:
    async def test_emit_durable_only_writes_log_not_fanout(self) -> None:
        fabric = InMemoryEventFabric()
        msg_id = await fabric.emit(
            PublishRequest(
                topic="t",
                partition_key="p",
                delivery_mode=EventDeliveryMode.DURABLE_LOG,
            ),
            {"hello": "world"},
        )
        assert msg_id is not None

        seen = []
        async for it in fabric.log.consume(
            ConsumeRequest(topic="t", partition_key="p", consumer_group="g", consumer_id="c")
        ):
            seen.append(it)
        assert len(seen) == 1

    async def test_emit_realtime_only_returns_none(self) -> None:
        fabric = InMemoryEventFabric()
        msg_id = await fabric.emit(
            PublishRequest(
                topic="t",
                partition_key="p",
                delivery_mode=EventDeliveryMode.REALTIME_FANOUT,
            ),
            {"hello": "world"},
        )
        assert msg_id is None

    async def test_emit_both_returns_log_message_id(self) -> None:
        fabric = InMemoryEventFabric()
        received: list[tuple[str, dict]] = []

        async def consume() -> None:
            async for item in fabric.fanout.subscribe(
                SubscribeRequest(topic_pattern="t", subscriber_id="s")
            ):
                received.append(item)
                return

        task = asyncio.create_task(consume())
        await asyncio.sleep(0.01)

        msg_id = await fabric.emit(
            PublishRequest(
                topic="t",
                partition_key="p",
                delivery_mode=EventDeliveryMode.BOTH,
            ),
            {"a": 1},
        )
        assert msg_id is not None
        await asyncio.wait_for(asyncio.sleep(0.05), timeout=1.0)
        assert received and received[0][1] == {"a": 1}
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def test_isinstance_runtime_protocol(self) -> None:
        fabric = InMemoryEventFabric()
        assert isinstance(fabric, EventFabric)
