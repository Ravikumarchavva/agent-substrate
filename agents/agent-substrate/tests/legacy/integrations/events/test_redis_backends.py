"""Tests for Redis-backed Event Fabric integrations.

All Redis I/O is mocked — no live Redis server required.

Coverage
--------
- Protocol conformance (isinstance checks)
- RedisStreamsDurableLog: publish, consume, ack, replay_from, multiple groups,
  connection errors
- RedisPubSubFanout: publish, subscribe + receive, unsubscribe, pattern sub,
  connection errors
- RedisLeaseRegistry: acquire, contention, release+re-acquire, renew, TTL expiry
  behaviour (expired lease → acquire succeeds), connection errors
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ravi.adapters.events import (
    RedisPubSubFanout,
    RedisLeaseRegistry,
    RedisStreamsDurableLog,
)
from ravi.kernel.events._fabric import (
    AckRequest,
    ConsumeRequest,
    DurableEventLog,
    PublishRequest,
    RealtimeFanout,
)
from ravi.kernel.runtime._identity import AgentId
from ravi.kernel.runtime._lease import LeaseRegistry
from ravi.kernel.runtime._lifecycle import ExecutionLease


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _pub_req(topic: str = "events", partition_key: str = "p1") -> PublishRequest:
    return PublishRequest(topic=topic, partition_key=partition_key)


def _con_req(
    topic: str = "events",
    partition_key: str = "p1",
    group: str = "grp",
    consumer: str = "c1",
    max_messages: int = 10,
    block_ms: int = 0,
) -> ConsumeRequest:
    return ConsumeRequest(
        topic=topic,
        partition_key=partition_key,
        consumer_group=group,
        consumer_id=consumer,
        max_messages=max_messages,
        block_ms=block_ms,
    )


def _agent_id(key: str = "agent-1") -> AgentId:
    return AgentId(type="test", key=key)


def _make_client_mock() -> AsyncMock:
    """Return a fully async-mocked redis client."""
    return AsyncMock()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_redis_client() -> AsyncMock:
    return _make_client_mock()


# ===========================================================================
# Protocol conformance
# ===========================================================================


class TestProtocolConformance:
    def test_durable_log_is_protocol(self) -> None:
        log = RedisStreamsDurableLog()
        assert isinstance(log, DurableEventLog)

    def test_fanout_is_protocol(self) -> None:
        fanout = RedisPubSubFanout()
        assert isinstance(fanout, RealtimeFanout)

    def test_lease_registry_is_protocol(self) -> None:
        reg = RedisLeaseRegistry()
        assert isinstance(reg, LeaseRegistry)


# ===========================================================================
# RedisStreamsDurableLog
# ===========================================================================


class TestRedisStreamsDurableLog:
    # ---- helpers --------------------------------------------------------

    def _make_log(self, mock_client: AsyncMock) -> RedisStreamsDurableLog:
        log = RedisStreamsDurableLog(redis_url="redis://localhost:6379/0")
        log._client = mock_client  # inject pre-built mock
        return log

    # ---- publish --------------------------------------------------------

    async def test_publish_calls_xadd_and_returns_message_id(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.xadd = AsyncMock(return_value="1718000000000-0")
        log = self._make_log(mock_redis_client)

        msg_id = await log.publish(_pub_req(), {"event": "created"})

        assert msg_id == "1718000000000-0"
        mock_redis_client.xadd.assert_called_once_with(
            "events:p1", {"payload": json.dumps({"event": "created"})}
        )

    async def test_publish_encodes_payload_as_json(
        self, mock_redis_client: AsyncMock
    ) -> None:
        captured: list[Any] = []

        async def capture_xadd(key: str, fields: dict[str, str]) -> str:
            captured.append(fields)
            return "1-0"

        mock_redis_client.xadd = capture_xadd
        log = self._make_log(mock_redis_client)
        payload = {"nested": {"x": 1}, "list": [1, 2, 3]}
        await log.publish(_pub_req(), payload)
        assert json.loads(captured[0]["payload"]) == payload

    async def test_publish_raises_on_connection_error(
        self, mock_redis_client: AsyncMock
    ) -> None:
        from redis.exceptions import ConnectionError as RedisConnError

        mock_redis_client.xadd = AsyncMock(side_effect=RedisConnError("down"))
        log = self._make_log(mock_redis_client)
        with pytest.raises(RedisConnError):
            await log.publish(_pub_req(), {"x": 1})

    # ---- consume --------------------------------------------------------

    async def test_consume_creates_group_and_yields_messages(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.xgroup_create = AsyncMock(return_value=True)
        mock_redis_client.xreadgroup = AsyncMock(
            return_value=[
                ("events:p1", [("1-0", {"payload": '{"val": 42}'})])
            ]
        )
        log = self._make_log(mock_redis_client)

        messages: list[tuple[str, dict[str, Any]]] = []
        async for mid, payload in log.consume(_con_req()):
            messages.append((mid, payload))

        assert messages == [("1-0", {"val": 42})]
        mock_redis_client.xgroup_create.assert_called_once_with(
            "events:p1", "grp", id="0", mkstream=True
        )
        mock_redis_client.xreadgroup.assert_called_once_with(
            "grp", "c1", {"events:p1": ">"}, count=10, block=None
        )

    async def test_consume_ignores_busygroup_error(
        self, mock_redis_client: AsyncMock
    ) -> None:
        from redis.exceptions import ResponseError

        mock_redis_client.xgroup_create = AsyncMock(
            side_effect=ResponseError("BUSYGROUP Consumer Group name already exists")
        )
        mock_redis_client.xreadgroup = AsyncMock(return_value=[])
        log = self._make_log(mock_redis_client)

        # Must not raise — just yield nothing
        messages = [m async for m in log.consume(_con_req())]
        assert messages == []

    async def test_consume_reraises_non_busygroup_error(
        self, mock_redis_client: AsyncMock
    ) -> None:
        from redis.exceptions import ResponseError

        mock_redis_client.xgroup_create = AsyncMock(
            side_effect=ResponseError("WRONGTYPE Operation against a key")
        )
        log = self._make_log(mock_redis_client)
        with pytest.raises(ResponseError, match="WRONGTYPE"):
            async for _ in log.consume(_con_req()):
                pass  # pragma: no cover

    async def test_consume_passes_block_ms_when_positive(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.xgroup_create = AsyncMock(return_value=True)
        mock_redis_client.xreadgroup = AsyncMock(return_value=[])
        log = self._make_log(mock_redis_client)

        async for _ in log.consume(_con_req(block_ms=500)):
            pass

        _, kwargs = mock_redis_client.xreadgroup.call_args
        assert kwargs["block"] == 500

    async def test_consume_no_results_yields_nothing(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.xgroup_create = AsyncMock(return_value=True)
        mock_redis_client.xreadgroup = AsyncMock(return_value=[])
        log = self._make_log(mock_redis_client)
        messages = [m async for m in log.consume(_con_req())]
        assert messages == []

    async def test_multiple_groups_get_same_messages(
        self, mock_redis_client: AsyncMock
    ) -> None:
        """Each consumer group receives the same stream messages independently."""
        mock_redis_client.xgroup_create = AsyncMock(return_value=True)
        mock_redis_client.xreadgroup = AsyncMock(
            return_value=[("events:p1", [("2-0", {"payload": '{"n": 7}'})])]
        )
        log = self._make_log(mock_redis_client)

        msgs_g1 = [m async for m in log.consume(_con_req(group="g1"))]
        msgs_g2 = [m async for m in log.consume(_con_req(group="g2"))]

        # Both groups see the same payload
        assert msgs_g1 == [("2-0", {"n": 7})]
        assert msgs_g2 == [("2-0", {"n": 7})]

    # ---- ack ------------------------------------------------------------

    async def test_ack_calls_xack_with_correct_args(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.xgroup_create = AsyncMock(return_value=True)
        mock_redis_client.xreadgroup = AsyncMock(
            return_value=[("events:p1", [("3-0", {"payload": '{"ok": true}'})])]
        )
        mock_redis_client.xack = AsyncMock(return_value=1)
        log = self._make_log(mock_redis_client)

        # Consume to register inflight
        async for _ in log.consume(_con_req(group="grp")):
            pass

        await log.ack(AckRequest(topic="events", consumer_group="grp", message_id="3-0"))

        mock_redis_client.xack.assert_called_once_with("events:p1", "grp", "3-0")

    async def test_ack_unknown_message_is_silent(
        self, mock_redis_client: AsyncMock
    ) -> None:
        log = self._make_log(mock_redis_client)
        # Should not raise even without prior consume
        await log.ack(
            AckRequest(topic="events", consumer_group="grp", message_id="unknown-0")
        )
        mock_redis_client.xack.assert_not_called()

    async def test_ack_advances_cursor_per_group(
        self, mock_redis_client: AsyncMock
    ) -> None:
        """Each group tracks its own inflight messages independently."""
        mock_redis_client.xgroup_create = AsyncMock(return_value=True)
        mock_redis_client.xreadgroup = AsyncMock(
            return_value=[("events:p1", [("5-0", {"payload": '{"x": 5}'})])]
        )
        mock_redis_client.xack = AsyncMock(return_value=1)
        log = self._make_log(mock_redis_client)

        async for _ in log.consume(_con_req(group="g1")):
            pass
        async for _ in log.consume(_con_req(group="g2")):
            pass

        await log.ack(AckRequest(topic="events", consumer_group="g1", message_id="5-0"))
        # g1 acked, g2 inflight still exists
        assert ("g2", "5-0") in log._inflight
        assert ("g1", "5-0") not in log._inflight

    # ---- replay_from ----------------------------------------------------

    async def test_replay_from_uses_xrange(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.xrange = AsyncMock(
            return_value=[
                ("10-0", {"payload": '{"seq": 0}'}),
                ("11-0", {"payload": '{"seq": 1}'}),
            ]
        )
        log = self._make_log(mock_redis_client)

        replayed = [m async for m in log.replay_from("events", "p1", "10-0", 50)]

        assert replayed == [("10-0", {"seq": 0}), ("11-0", {"seq": 1})]
        mock_redis_client.xrange.assert_called_once_with(
            "events:p1", min="10-0", max="+", count=50
        )

    async def test_replay_from_empty_offset_uses_start(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.xrange = AsyncMock(return_value=[])
        log = self._make_log(mock_redis_client)

        _ = [m async for m in log.replay_from("events", "p1", "", 10)]

        mock_redis_client.xrange.assert_called_once_with(
            "events:p1", min="-", max="+", count=10
        )

    # ---- lazy client init -----------------------------------------------

    async def test_client_created_lazily_on_first_use(self) -> None:
        with patch("ravi.adapters.events._redis_log.aioredis") as mock_aioredis:
            mock_client = AsyncMock()
            mock_client.xadd = AsyncMock(return_value="1-0")
            mock_aioredis.from_url.return_value = mock_client

            log = RedisStreamsDurableLog(redis_url="redis://test:6379/0")
            assert log._client is None

            await log.publish(_pub_req(), {})

            mock_aioredis.from_url.assert_called_once_with(
                "redis://test:6379/0", decode_responses=True
            )
            assert log._client is mock_client


# ===========================================================================
# RedisPubSubFanout
# ===========================================================================


def _sub_req(
    topic_pattern: str = "test-topic",
    subscriber_id: str = "sub-1",
) -> Any:
    from ravi.kernel.events._fabric import SubscribeRequest

    return SubscribeRequest(topic_pattern=topic_pattern, subscriber_id=subscriber_id)


class TestRedisPubSubFanout:
    def _make_fanout(self, mock_client: AsyncMock) -> RedisPubSubFanout:
        fanout = RedisPubSubFanout()
        fanout._client = mock_client
        return fanout

    # ---- publish --------------------------------------------------------

    async def test_publish_calls_redis_publish(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.publish = AsyncMock(return_value=1)
        fanout = self._make_fanout(mock_redis_client)

        await fanout.publish(_pub_req(topic="my-topic"), {"x": 99})

        mock_redis_client.publish.assert_called_once_with(
            "my-topic", json.dumps({"x": 99})
        )

    async def test_publish_raises_on_connection_error(
        self, mock_redis_client: AsyncMock
    ) -> None:
        from redis.exceptions import ConnectionError as RedisConnError

        mock_redis_client.publish = AsyncMock(side_effect=RedisConnError("down"))
        fanout = self._make_fanout(mock_redis_client)

        with pytest.raises(RedisConnError):
            await fanout.publish(_pub_req(), {"x": 1})

    # ---- subscribe / receive --------------------------------------------

    async def test_subscribe_calls_redis_subscribe(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.get_message = AsyncMock(return_value=None)
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.aclose = AsyncMock()
        mock_redis_client.pubsub = MagicMock(return_value=mock_pubsub)

        fanout = self._make_fanout(mock_redis_client)
        req = _sub_req(topic_pattern="test-topic")

        gen = fanout.subscribe(req)
        # Prime the generator — trigger subscribe call.
        task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0)  # let generator run up to first get_message
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, StopAsyncIteration):
            pass

        mock_pubsub.subscribe.assert_called_once_with("test-topic")

    async def test_subscribe_uses_psubscribe_for_pattern(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_pubsub = AsyncMock()
        mock_pubsub.psubscribe = AsyncMock()
        mock_pubsub.get_message = AsyncMock(return_value=None)
        mock_pubsub.punsubscribe = AsyncMock()
        mock_pubsub.aclose = AsyncMock()
        mock_redis_client.pubsub = MagicMock(return_value=mock_pubsub)

        fanout = self._make_fanout(mock_redis_client)
        req = _sub_req(topic_pattern="agent.*")

        gen = fanout.subscribe(req)
        task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, StopAsyncIteration):
            pass

        mock_pubsub.psubscribe.assert_called_once_with("agent.*")

    async def test_subscriber_receives_published_message(
        self, mock_redis_client: AsyncMock
    ) -> None:
        """get_message returns a message; generator yields it."""
        msg_payload = {"event": "ping", "val": 1}
        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.get_message = AsyncMock(
            return_value={
                "type": "message",
                "channel": "test-topic",
                "data": json.dumps(msg_payload),
            }
        )
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.aclose = AsyncMock()
        mock_redis_client.pubsub = MagicMock(return_value=mock_pubsub)

        fanout = self._make_fanout(mock_redis_client)
        req = _sub_req()

        gen = fanout.subscribe(req)
        # Retrieve exactly one message then break.
        topic, payload = await gen.__anext__()
        await gen.aclose()

        assert topic == "test-topic"
        assert payload == msg_payload

    async def test_subscribe_ignores_non_message_types(
        self, mock_redis_client: AsyncMock
    ) -> None:
        """Control messages (subscribe/pong) must be skipped; only data yielded."""
        msg_payload = {"k": "v"}
        responses = [
            {"type": "subscribe", "channel": "test-topic", "data": 1},
            {
                "type": "message",
                "channel": "test-topic",
                "data": json.dumps(msg_payload),
            },
        ]
        call_count = 0

        async def get_msg(ignore_subscribe_messages: bool = False, timeout: float = 0) -> Any:
            nonlocal call_count
            if call_count < len(responses):
                r = responses[call_count]
                call_count += 1
                return r
            return None

        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.get_message = get_msg
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.aclose = AsyncMock()
        mock_redis_client.pubsub = MagicMock(return_value=mock_pubsub)

        fanout = self._make_fanout(mock_redis_client)
        gen = fanout.subscribe(_sub_req())

        topic, payload = await gen.__anext__()
        await gen.aclose()

        assert payload == msg_payload

    # ---- unsubscribe ----------------------------------------------------

    async def test_unsubscribe_sets_cancel_event(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_pubsub = AsyncMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.get_message = AsyncMock(return_value=None)
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.aclose = AsyncMock()
        mock_redis_client.pubsub = MagicMock(return_value=mock_pubsub)

        fanout = self._make_fanout(mock_redis_client)
        req = _sub_req(subscriber_id="sub-x")

        # Start the generator far enough to register the subscription.
        gen = fanout.subscribe(req)
        task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0)

        # At this point the subscription should be registered.
        assert "sub-x" in fanout._subscriptions

        await fanout.unsubscribe("sub-x")
        # Cancel event should now be set.
        _pubsub, cancel_event = fanout._subscriptions.get("sub-x", (None, None))
        # After unsubscribe the event is set (entry may still exist until
        # generator resumes and exits the loop).
        assert cancel_event is None or cancel_event.is_set()

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, StopAsyncIteration):
            pass

    async def test_unsubscribe_unknown_subscriber_is_noop(
        self, mock_redis_client: AsyncMock
    ) -> None:
        fanout = self._make_fanout(mock_redis_client)
        # Should not raise
        await fanout.unsubscribe("non-existent-sub")

    # ---- lazy client init -----------------------------------------------

    async def test_client_created_lazily(self) -> None:
        with patch("ravi.adapters.events._redis_fanout.aioredis") as mock_aioredis:
            mock_client = AsyncMock()
            mock_client.publish = AsyncMock(return_value=0)
            mock_aioredis.from_url.return_value = mock_client

            fanout = RedisPubSubFanout(redis_url="redis://test:6379/1")
            assert fanout._client is None

            await fanout.publish(_pub_req(topic="t"), {})

            mock_aioredis.from_url.assert_called_once_with(
                "redis://test:6379/1", decode_responses=True
            )


# ===========================================================================
# RedisLeaseRegistry
# ===========================================================================


def _make_registry(mock_client: AsyncMock) -> RedisLeaseRegistry:
    reg = RedisLeaseRegistry(key_prefix="ravi:lease:")
    reg._client = mock_client
    return reg


def _acquired_json(
    agent_id: AgentId,
    worker_id: str = "worker-1",
    lease_id: str = "lid-1",
) -> str:
    """Minimal valid lease JSON."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    return json.dumps(
        {
            "agent_id_str": str(agent_id),
            "worker_id": worker_id,
            "lease_id": lease_id,
            "granted_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=60)).isoformat(),
            "budget_tokens": 0,
            "budget_steps": 0,
        }
    )


class TestRedisLeaseRegistry:
    # ---- acquire --------------------------------------------------------

    async def test_acquire_returns_lease_when_key_absent(
        self, mock_redis_client: AsyncMock
    ) -> None:
        agent = _agent_id()
        # Lua returns [1, <stored_json>]
        mock_redis_client.eval = AsyncMock(
            side_effect=lambda script, numkeys, *args: _eval_acquire_ok(*args)
        )
        reg = _make_registry(mock_redis_client)

        result = await reg.acquire(agent, "worker-1", ttl_seconds=30.0)

        assert result.acquired
        assert result.lease is not None
        assert result.lease.worker_id == "worker-1"
        assert result.lease.agent_id_str == str(agent)

    async def test_acquire_fails_when_key_exists(
        self, mock_redis_client: AsyncMock
    ) -> None:
        agent = _agent_id("agent-2")
        existing_json = _acquired_json(agent, worker_id="other-worker", lease_id="old")
        # Lua returns [0, <existing_json>]
        mock_redis_client.eval = AsyncMock(return_value=[0, existing_json])
        reg = _make_registry(mock_redis_client)

        result = await reg.acquire(agent, "worker-new")

        assert not result.acquired
        assert result.current_holder is not None
        assert result.current_holder.worker_id == "other-worker"

    async def test_acquire_parses_corrupt_existing_json_gracefully(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.eval = AsyncMock(return_value=[0, "not-valid-json"])
        reg = _make_registry(mock_redis_client)

        result = await reg.acquire(_agent_id(), "worker-1")

        assert not result.acquired
        assert result.current_holder is None  # corrupt JSON → None

    async def test_acquire_raises_on_connection_error(
        self, mock_redis_client: AsyncMock
    ) -> None:
        from redis.exceptions import ConnectionError as RedisConnError

        mock_redis_client.eval = AsyncMock(side_effect=RedisConnError("down"))
        reg = _make_registry(mock_redis_client)

        with pytest.raises(RedisConnError):
            await reg.acquire(_agent_id(), "w1")

    # ---- release + re-acquire -------------------------------------------

    async def test_release_then_reacquire_succeeds(
        self, mock_redis_client: AsyncMock
    ) -> None:
        agent = _agent_id("agent-3")
        lease = ExecutionLease(
            agent_id_str=str(agent),
            worker_id="worker-1",
            lease_id="lid-abc",
            granted_at="2026-01-01T00:00:00+00:00",
            expires_at="2026-01-01T00:01:00+00:00",
        )

        # release → Lua returns 1 (deleted)
        mock_redis_client.eval = AsyncMock(return_value=1)
        reg = _make_registry(mock_redis_client)
        await reg.release(lease)  # should succeed without error

        # re-acquire after release → Lua returns [1, json]
        mock_redis_client.eval = AsyncMock(
            side_effect=lambda script, numkeys, *args: _eval_acquire_ok(*args)
        )
        result = await reg.acquire(agent, "worker-2")
        assert result.acquired

    async def test_release_noop_when_different_holder(
        self, mock_redis_client: AsyncMock
    ) -> None:
        # Lua returns 0 — different holder, should not raise
        mock_redis_client.eval = AsyncMock(return_value=0)
        reg = _make_registry(mock_redis_client)
        lease = ExecutionLease(
            agent_id_str="test/agent-4",
            worker_id="stale-worker",
            lease_id="stale-id",
            granted_at="2026-01-01T00:00:00+00:00",
            expires_at="2026-01-01T00:01:00+00:00",
        )
        # Should not raise
        await reg.release(lease)

    # ---- renew ----------------------------------------------------------

    async def test_renew_returns_updated_lease_on_success(
        self, mock_redis_client: AsyncMock
    ) -> None:
        agent = _agent_id("agent-5")
        lease = ExecutionLease(
            agent_id_str=str(agent),
            worker_id="worker-1",
            lease_id="lid-1",
            granted_at="2026-01-01T00:00:00+00:00",
            expires_at="2026-01-01T00:01:00+00:00",
        )
        # Lua returns [1, <renewed_json>]
        mock_redis_client.eval = AsyncMock(
            side_effect=lambda script, numkeys, *args: _eval_renew_ok(*args)
        )
        reg = _make_registry(mock_redis_client)

        renewed = await reg.renew(lease, ttl_seconds=60.0)

        assert renewed is not None
        assert renewed.lease_id == "lid-1"
        assert renewed.worker_id == "worker-1"

    async def test_renew_returns_none_when_lease_expired(
        self, mock_redis_client: AsyncMock
    ) -> None:
        # Lua returns [0, ""] — key gone (expired)
        mock_redis_client.eval = AsyncMock(return_value=[0, ""])
        reg = _make_registry(mock_redis_client)
        lease = ExecutionLease(
            agent_id_str="test/agent-6",
            worker_id="w",
            lease_id="l",
            granted_at="2026-01-01T00:00:00+00:00",
            expires_at="2026-01-01T00:01:00+00:00",
        )
        result = await reg.renew(lease)
        assert result is None

    async def test_renew_returns_none_when_different_holder(
        self, mock_redis_client: AsyncMock
    ) -> None:
        agent = _agent_id("agent-7")
        other_json = _acquired_json(agent, worker_id="other-worker")
        # Lua returns [0, <other_holder_json>]
        mock_redis_client.eval = AsyncMock(return_value=[0, other_json])
        reg = _make_registry(mock_redis_client)
        lease = ExecutionLease(
            agent_id_str=str(agent),
            worker_id="my-worker",
            lease_id="l",
            granted_at="2026-01-01T00:00:00+00:00",
            expires_at="2026-01-01T00:01:00+00:00",
        )
        result = await reg.renew(lease)
        assert result is None

    # ---- current --------------------------------------------------------

    async def test_current_returns_lease_when_key_exists(
        self, mock_redis_client: AsyncMock
    ) -> None:
        agent = _agent_id("agent-8")
        stored_json = _acquired_json(agent, worker_id="w1", lease_id="l1")
        mock_redis_client.get = AsyncMock(return_value=stored_json)
        reg = _make_registry(mock_redis_client)

        lease = await reg.current(agent)

        assert lease is not None
        assert lease.worker_id == "w1"
        assert lease.lease_id == "l1"
        mock_redis_client.get.assert_called_once_with(f"ravi:lease:{agent}")

    async def test_current_returns_none_when_key_absent(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.get = AsyncMock(return_value=None)
        reg = _make_registry(mock_redis_client)

        result = await reg.current(_agent_id("missing"))
        assert result is None

    async def test_current_returns_none_on_corrupt_json(
        self, mock_redis_client: AsyncMock
    ) -> None:
        mock_redis_client.get = AsyncMock(return_value="{bad json}")
        reg = _make_registry(mock_redis_client)
        result = await reg.current(_agent_id())
        assert result is None

    # ---- TTL expiry simulation ------------------------------------------

    async def test_acquire_succeeds_after_ttl_expiry(
        self, mock_redis_client: AsyncMock
    ) -> None:
        """After expiry, Redis returns nil for GET so acquire should succeed."""
        agent = _agent_id("agent-9")
        # First acquire: success
        first_call = True

        def lua_side_effect(script: str, numkeys: int, *args: Any) -> Any:
            nonlocal first_call
            if first_call:
                first_call = False
                return _eval_acquire_ok(*args)
            # TTL expired — key is gone, second acquire succeeds too
            return _eval_acquire_ok(*args)

        mock_redis_client.eval = AsyncMock(side_effect=lua_side_effect)
        reg = _make_registry(mock_redis_client)

        r1 = await reg.acquire(agent, "worker-A")
        assert r1.acquired

        # Simulate TTL expiry: next acquire also succeeds
        r2 = await reg.acquire(agent, "worker-B")
        assert r2.acquired

    # ---- lazy client init -----------------------------------------------

    async def test_client_created_lazily(self) -> None:
        with patch("ravi.adapters.events._redis_lease.aioredis") as mock_aioredis:
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=None)
            mock_aioredis.from_url.return_value = mock_client

            reg = RedisLeaseRegistry(redis_url="redis://test:6379/2")
            assert reg._client is None

            await reg.current(_agent_id())

            mock_aioredis.from_url.assert_called_once_with(
                "redis://test:6379/2", decode_responses=True
            )


# ---------------------------------------------------------------------------
# Lua eval helpers — simulate the Lua scripts returning expected shapes
# ---------------------------------------------------------------------------


def _eval_acquire_ok(*args: Any) -> list[Any]:
    """Simulate acquire Lua script success: parse stored value from ARGV[1]."""
    # args: (key, lease_json, ttl_ms_str)
    lease_json = args[1] if len(args) > 1 else "{}"
    return [1, lease_json]


def _eval_renew_ok(*args: Any) -> list[Any]:
    """Simulate renew Lua script success: return [1, renewed_json]."""
    # args: (key, worker_id, renewed_json, ttl_ms_str)
    renewed_json = args[2] if len(args) > 2 else "{}"
    return [1, renewed_json]
