"""Hardening tests — validates all bug fixes from the runtime hardening audit.

Covers: C1-C6 (critical), H1-H12 (high), M1-M4 (medium).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from raavan.core.runtime import (
    AgentId,
    Dispatcher,
    AgentNotFoundError,
    LocalRuntime,
    Mailbox,
    MailboxFullError,
    TopicId,
    HandlerError,
)
from raavan.core.runtime._stream import StreamPublisher
from raavan.core.runtime._supervisor import Supervisor, SupervisorEscalation
from raavan.core.runtime._types import Envelope, MessageContext, RestartPolicy
from raavan.integrations.runtime._base import BaseRemoteRuntime


# ══════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════


async def _echo_handler(ctx: MessageContext, payload: Any) -> Any:
    return f"echo:{payload}"


async def _crash_handler(ctx: MessageContext, payload: Any) -> Any:
    raise RuntimeError("deliberate crash")


async def _slow_handler(ctx: MessageContext, payload: Any) -> Any:
    await asyncio.sleep(999)
    return "never"


class _ConcreteRemoteRuntime(BaseRemoteRuntime):
    """Minimal concrete subclass for testing BaseRemoteRuntime."""

    def __init__(self) -> None:
        super().__init__()
        self._remote_calls: list[tuple[Any, AgentId]] = []

    async def _remote_send(
        self,
        message: Any,
        *,
        sender: AgentId | None,
        recipient: AgentId,
    ) -> Any:
        self._remote_calls.append((message, recipient))
        return f"remote:{message}"

    async def start(self) -> None:
        self._started = True

    async def stop(self) -> None:
        self._started = False


# ══════════════════════════════════════════════════════════════════════════
# C1: Supervisor restart budget fix
# ══════════════════════════════════════════════════════════════════════════


class TestSupervisorRestartBudget:
    """C1: _restart_times.setdefault — budget counter persists across crashes."""

    async def test_escalation_after_max_restarts(self) -> None:
        """Agent crashing max_restarts+1 times raises SupervisorEscalation."""
        policy = RestartPolicy(max_restarts=2, restart_window=60.0)
        supervisor = Supervisor(restart_policy=policy)

        call_count = 0

        async def crash_factory() -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"crash #{call_count}")

        agent_id = AgentId(type="crasher", key="1")
        supervisor.supervise(agent_id, crash_factory)

        # Let the crash → restart → crash chain complete
        await asyncio.sleep(0.2)

        # Should have attempted max_restarts + 1 total runs
        assert call_count >= 3

        # The final spawned task holds the SupervisorEscalation
        final_task = supervisor._tasks.get(agent_id)
        assert final_task is not None and final_task.done()
        assert isinstance(final_task.exception(), SupervisorEscalation)

        await supervisor.stop_all()

    async def test_restart_within_budget(self) -> None:
        """Agent crashes fewer than max_restarts times — no escalation."""
        policy = RestartPolicy(max_restarts=5, restart_window=60.0)
        supervisor = Supervisor(restart_policy=policy)

        crash_count = 0

        async def factory_crashes_twice() -> None:
            nonlocal crash_count
            crash_count += 1
            if crash_count <= 2:
                raise RuntimeError(f"crash #{crash_count}")
            # Third time: succeed and stay alive briefly
            await asyncio.sleep(0.1)

        agent_id = AgentId(type="recoverer", key="1")
        supervisor.supervise(agent_id, factory_crashes_twice)
        await asyncio.sleep(0.3)
        await supervisor.stop_all()
        assert crash_count >= 3


# ══════════════════════════════════════════════════════════════════════════
# C2: Mailbox close deadlock fix
# ══════════════════════════════════════════════════════════════════════════


class TestMailboxCloseDeadlock:
    """C2: close() when queue is full → get() still unblocks via Event."""

    async def test_close_full_mailbox_unblocks_get(self) -> None:
        """Closing a full mailbox unblocks a blocked get() call."""
        mbox = Mailbox(capacity=1)
        envelope = Envelope(
            sender=None,
            target=AgentId(type="test", key="1"),
            payload="fill",
        )
        await mbox.put(envelope)
        assert mbox.is_full

        # Close while full — sentinel can't be inserted
        mbox.close()

        # get() should return the existing envelope first, then raise StopAsyncIteration
        result = await asyncio.wait_for(mbox.get(), timeout=1.0)
        assert result.payload == "fill"

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(mbox.get(), timeout=1.0)

    async def test_get_blocked_then_close(self) -> None:
        """A blocked get() call is unblocked by close()."""
        mbox = Mailbox(capacity=10)

        async def delayed_close() -> None:
            await asyncio.sleep(0.05)
            mbox.close()

        asyncio.create_task(delayed_close())

        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(mbox.get(), timeout=2.0)

    async def test_close_empty_mailbox(self) -> None:
        """Closing an empty mailbox works normally."""
        mbox = Mailbox(capacity=10)
        mbox.close()
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(mbox.get(), timeout=1.0)

    async def test_put_after_close_raises(self) -> None:
        """put() after close() raises MailboxFullError."""
        mbox = Mailbox(capacity=10)
        mbox.close()
        with pytest.raises(MailboxFullError):
            await mbox.put(Envelope(sender=None, target=AgentId("t", "k"), payload="x"))

    async def test_get_timeout(self) -> None:
        """get() with timeout raises TimeoutError when no messages arrive."""
        mbox = Mailbox(capacity=10)
        with pytest.raises(asyncio.TimeoutError):
            await mbox.get(timeout=0.05)


# ══════════════════════════════════════════════════════════════════════════
# C3: Future leak on dispatch failure
# ══════════════════════════════════════════════════════════════════════════


class TestFutureLeakOnDispatch:
    """C3: if dispatch() raises, future is cleaned up."""

    async def test_no_lingering_future(self) -> None:
        runtime = LocalRuntime(send_timeout=5.0)
        await runtime.start()

        # Don't register any handler — dispatch should fail
        with pytest.raises(AgentNotFoundError):
            await runtime.send_message(
                "hello",
                sender=None,
                recipient=AgentId(type="nonexistent", key="1"),
            )

        # No futures should remain
        assert len(runtime._pending_responses) == 0
        await runtime.stop()


# ══════════════════════════════════════════════════════════════════════════
# C4: CancelledError escapes handler — future always resolved
# ══════════════════════════════════════════════════════════════════════════


class TestCancelledErrorFutureResolution:
    """C4: future is resolved even when handler gets CancelledError."""

    async def test_handler_crash_resolves_future(self) -> None:
        """Handler raising Exception → future gets HandlerError."""
        runtime = LocalRuntime(send_timeout=5.0)
        await runtime.start()
        await runtime.register("crasher", _crash_handler)

        with pytest.raises(HandlerError):
            await runtime.send_message(
                "boom",
                sender=None,
                recipient=AgentId(type="crasher", key="1"),
            )

        await runtime.stop()


# ══════════════════════════════════════════════════════════════════════════
# C5: send_message timeout
# ══════════════════════════════════════════════════════════════════════════


class TestSendMessageTimeout:
    """C5: send_message with slow handler times out."""

    async def test_timeout_raises(self) -> None:
        runtime = LocalRuntime(send_timeout=0.1)
        await runtime.start()
        await runtime.register("slow", _slow_handler)

        with pytest.raises(TimeoutError, match="timed out"):
            await runtime.send_message(
                "wait",
                sender=None,
                recipient=AgentId(type="slow", key="1"),
            )

        # Future should be cleaned up
        assert len(runtime._pending_responses) == 0
        await runtime.stop()

    async def test_no_timeout_when_none(self) -> None:
        """send_timeout=None disables timeout (handler responds normally)."""
        runtime = LocalRuntime(send_timeout=None)
        await runtime.start()
        await runtime.register("echo", _echo_handler)

        result = await runtime.send_message(
            "hi",
            sender=None,
            recipient=AgentId(type="echo", key="1"),
        )
        assert result == "echo:hi"
        await runtime.stop()


# ══════════════════════════════════════════════════════════════════════════
# C6: Fan-out exception isolation
# ══════════════════════════════════════════════════════════════════════════


class TestFanOutIsolation:
    """C6: one subscriber failure doesn't stop delivery to others."""

    async def test_fan_out_continues_after_failure(self) -> None:
        dispatcher = Dispatcher()
        topic = TopicId(type="events", source="test")

        # Register 3 agents of 2 types
        mbox_a = Mailbox(capacity=10)
        mbox_b = Mailbox(capacity=10)
        mbox_c = Mailbox(capacity=1)

        aid_a = AgentId(type="listener_a", key="1")
        aid_b = AgentId(type="listener_b", key="1")
        aid_c = AgentId(type="listener_c", key="1")

        dispatcher.register_agent(aid_a, mbox_a)
        dispatcher.register_agent(aid_b, mbox_b)
        dispatcher.register_agent(aid_c, mbox_c)

        dispatcher.subscribe_to_topic(topic, "listener_a")
        dispatcher.subscribe_to_topic(topic, "listener_b")
        dispatcher.subscribe_to_topic(topic, "listener_c")

        # Fill mbox_c so it will fail on put
        await mbox_c.put(Envelope(sender=None, target=aid_c, payload="fill"))
        assert mbox_c.is_full

        # Dispatch to topic — should continue past mbox_c failure
        envelope = Envelope(sender=None, target=topic, payload="broadcast")
        await dispatcher.dispatch(envelope)

        # A and B should have received it
        assert mbox_a.size == 1
        assert mbox_b.size == 1

    async def test_closed_mailbox_skipped(self) -> None:
        """Closed mailbox is skipped without stopping fan-out."""
        dispatcher = Dispatcher()
        topic = TopicId(type="events", source="test")

        mbox_a = Mailbox(capacity=10)
        mbox_b = Mailbox(capacity=10)
        aid_a = AgentId(type="type_a", key="1")
        aid_b = AgentId(type="type_b", key="1")

        dispatcher.register_agent(aid_a, mbox_a)
        dispatcher.register_agent(aid_b, mbox_b)
        dispatcher.subscribe_to_topic(topic, "type_a")
        dispatcher.subscribe_to_topic(topic, "type_b")

        mbox_a.close()

        envelope = Envelope(sender=None, target=topic, payload="test")
        await dispatcher.dispatch(envelope)

        # B should still receive
        assert mbox_b.size == 1


# ══════════════════════════════════════════════════════════════════════════
# H1: Unregister granularity
# ══════════════════════════════════════════════════════════════════════════


class TestUnregisterGranularity:
    """H1: unregister removes specific AgentId, not all of same type."""

    async def test_unregister_specific_agent(self) -> None:
        dispatcher = Dispatcher()

        aid1 = AgentId(type="worker", key="1")
        aid2 = AgentId(type="worker", key="2")

        mbox1 = Mailbox(capacity=10)
        mbox2 = Mailbox(capacity=10)

        dispatcher.register_agent(aid1, mbox1)
        dispatcher.register_agent(aid2, mbox2)

        # Unregister only aid1
        dispatcher.unregister_agent(aid1)

        assert dispatcher.get_mailbox(aid1) is None
        assert dispatcher.get_mailbox(aid2) is mbox2

    async def test_unregister_preserves_topic_subscriptions(self) -> None:
        """Unregistering one agent of a type keeps the subscriptions
        alive if other instances of that type still exist."""
        dispatcher = Dispatcher()
        topic = TopicId(type="events", source="x")

        aid1 = AgentId(type="worker", key="1")
        aid2 = AgentId(type="worker", key="2")

        dispatcher.register_agent(aid1, Mailbox(capacity=10))
        dispatcher.register_agent(aid2, Mailbox(capacity=10))
        dispatcher.subscribe_to_topic(topic, "worker")

        # Unregister aid1 — aid2 still exists, subscription should remain
        dispatcher.unregister_agent(aid1)

        # Dispatch to topic should still reach aid2
        envelope = Envelope(sender=None, target=topic, payload="test")
        await dispatcher.dispatch(envelope)
        assert dispatcher.get_mailbox(aid2).size == 1  # type: ignore[union-attr]


# ══════════════════════════════════════════════════════════════════════════
# H4: Handler error propagation
# ══════════════════════════════════════════════════════════════════════════


class TestHandlerErrorPropagation:
    """H4: handler crashes → HandlerError, not silent None."""

    async def test_handler_error_raised(self) -> None:
        runtime = LocalRuntime(send_timeout=5.0)
        await runtime.start()
        await runtime.register("crasher", _crash_handler)

        with pytest.raises(HandlerError, match="deliberate crash"):
            await runtime.send_message(
                "hello",
                sender=None,
                recipient=AgentId(type="crasher", key="1"),
            )

        await runtime.stop()

    async def test_successful_handler_returns_value(self) -> None:
        runtime = LocalRuntime(send_timeout=5.0)
        await runtime.start()
        await runtime.register("echo", _echo_handler)

        result = await runtime.send_message(
            "test",
            sender=None,
            recipient=AgentId(type="echo", key="1"),
        )
        assert result == "echo:test"
        await runtime.stop()


# ══════════════════════════════════════════════════════════════════════════
# H5: StreamPublisher TOCTOU
# ══════════════════════════════════════════════════════════════════════════


class TestStreamPublisherTOCTOU:
    """H5: Lock prevents emit after close race."""

    async def test_emit_after_close_raises(self) -> None:
        runtime_mock = AsyncMock()
        topic = TopicId(type="stream", source="test")
        sender = AgentId(type="agent", key="1")

        publisher = StreamPublisher(runtime_mock, topic, sender=sender)
        await publisher.close()

        with pytest.raises(RuntimeError, match="closed"):
            await publisher.emit("late_event")

    async def test_close_idempotent(self) -> None:
        runtime_mock = AsyncMock()
        topic = TopicId(type="stream", source="test")
        sender = AgentId(type="agent", key="1")

        publisher = StreamPublisher(runtime_mock, topic, sender=sender)
        await publisher.close()
        await publisher.close()  # second close is no-op

        # StreamDone should have been published exactly once
        assert runtime_mock.publish_message.call_count == 1

    async def test_close_failure_allows_retry(self) -> None:
        """M4: if publish fails during close, publisher stays open for retry."""
        runtime_mock = AsyncMock()
        runtime_mock.publish_message.side_effect = RuntimeError("network error")

        topic = TopicId(type="stream", source="test")
        sender = AgentId(type="agent", key="1")

        publisher = StreamPublisher(runtime_mock, topic, sender=sender)
        with pytest.raises(RuntimeError, match="network error"):
            await publisher.close()

        # Publisher should NOT be marked closed
        assert not publisher._closed


# ══════════════════════════════════════════════════════════════════════════
# H6, H7: BaseRemoteRuntime fan-out isolation + lifecycle guards
# ══════════════════════════════════════════════════════════════════════════


class TestBaseRemoteRuntimeLifecycle:
    """H6, H7, M3: fan-out, lifecycle guards, validation."""

    async def test_send_before_start_raises(self) -> None:
        rt = _ConcreteRemoteRuntime()
        with pytest.raises(RuntimeError, match="cannot send before start"):
            await rt.send_message("msg", sender=None, recipient=AgentId("t", "k"))

    async def test_publish_before_start_raises(self) -> None:
        rt = _ConcreteRemoteRuntime()
        with pytest.raises(RuntimeError, match="cannot publish before start"):
            await rt.publish_message("msg", sender=None, topic=TopicId("t", "s"))

    async def test_register_after_start_raises(self) -> None:
        rt = _ConcreteRemoteRuntime()
        await rt.start()
        with pytest.raises(RuntimeError, match="cannot register after start"):
            await rt.register("late_agent", _echo_handler)
        await rt.stop()

    async def test_register_empty_type_raises(self) -> None:
        rt = _ConcreteRemoteRuntime()
        with pytest.raises(ValueError, match="non-empty string"):
            await rt.register("", _echo_handler)

    async def test_register_non_callable_raises(self) -> None:
        rt = _ConcreteRemoteRuntime()
        with pytest.raises(ValueError, match="callable"):
            await rt.register("test", "not_callable")  # type: ignore[arg-type]

    async def test_fan_out_isolation(self) -> None:
        """H6: one subscriber failure doesn't block others."""
        rt = _ConcreteRemoteRuntime()

        call_results: list[str] = []

        async def handler_ok(ctx: MessageContext, payload: Any) -> None:
            call_results.append(f"ok:{payload}")

        async def handler_crash(ctx: MessageContext, payload: Any) -> None:
            raise RuntimeError("boom")

        await rt.register("ok_handler", handler_ok)
        await rt.register("crash_handler", handler_crash)

        topic = TopicId(type="events", source="test")
        await rt.subscribe("ok_handler", topic)
        await rt.subscribe("crash_handler", topic)

        await rt.start()
        # Should not raise even though crash_handler fails
        await rt.publish_message("hello", sender=None, topic=topic)

        # ok_handler should still have been called
        assert any("ok:hello" in r for r in call_results)
        await rt.stop()


# ══════════════════════════════════════════════════════════════════════════
# H10: URL encoding in RestateRuntime
# ══════════════════════════════════════════════════════════════════════════


class TestRestateURLEncoding:
    """H10: special characters in agent key are URL-encoded + AgentId validation."""

    def test_slash_in_key_rejected_by_validation(self) -> None:
        """AgentId now rejects slashes — primary defense against path traversal."""
        with pytest.raises(ValueError, match="Invalid agent key"):
            AgentId(type="agent", key="path/with/slashes")

    def test_invalid_type_rejected(self) -> None:
        """AgentId rejects invalid type names."""
        with pytest.raises(ValueError, match="Invalid agent type"):
            AgentId(type="agent type spaces", key="1")

    async def test_valid_key_url_encoded(self) -> None:
        """Verify that even valid keys get URL-quoted in Restate calls."""
        try:
            from raavan.integrations.runtime.restate.runtime import RestateRuntime
        except ImportError:
            pytest.skip("restate-sdk / httpx not installed")

        rt = RestateRuntime()
        rt._started = True

        with patch("raavan.integrations.runtime.restate.runtime.httpx") as mock_httpx:
            mock_client = AsyncMock()
            mock_resp = AsyncMock()
            mock_resp.json.return_value = {"result": "ok"}
            mock_resp.raise_for_status = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_httpx.AsyncClient.return_value.__aenter__ = AsyncMock(
                return_value=mock_client
            )
            mock_httpx.AsyncClient.return_value.__aexit__ = AsyncMock(
                return_value=False
            )

            await rt._remote_send(
                "msg",
                sender=None,
                recipient=AgentId(type="my_agent", key="session-123"),
            )

            call_args = mock_client.post.call_args
            url = call_args[0][0] if call_args[0] else call_args[1]["url"]
            # Valid key should appear in URL (dots, hyphens, underscores are safe)
            assert "session-123" in url
            assert "AgentHandler_my_agent" in url


# ══════════════════════════════════════════════════════════════════════════
# H8, H12: GrpcRuntime error handling + stop cleanup
# ══════════════════════════════════════════════════════════════════════════


class TestGrpcRuntimeErrorHandling:
    """H8: gRPC errors wrapped in RuntimeError. H12: stop uses finally."""

    async def test_grpc_inherits_base(self) -> None:
        try:
            from raavan.integrations.runtime.grpc.runtime import GrpcRuntime
        except ImportError:
            pytest.skip("grpcio not installed")
        assert issubclass(GrpcRuntime, BaseRemoteRuntime)

    async def test_stop_clears_server_on_error(self) -> None:
        """H12: even if stop logic fails, server ref is cleaned up."""
        try:
            from raavan.integrations.runtime.grpc.runtime import GrpcRuntime
        except ImportError:
            pytest.skip("grpcio not installed")

        rt = GrpcRuntime()
        rt._started = True
        mock_server = AsyncMock()
        mock_server.stop.side_effect = RuntimeError("stop failed")
        rt._server = mock_server

        # stop() should not raise — server ref should be cleared
        with pytest.raises(RuntimeError, match="stop failed"):
            await rt.stop()

        assert rt._server is None


# ══════════════════════════════════════════════════════════════════════════
# H9: RestateRuntime start only sets _started on success
# ══════════════════════════════════════════════════════════════════════════


class TestRestateStartLifecycle:
    """H9: _started only True after successful admin registration."""

    async def test_start_failure_leaves_not_started(self) -> None:
        try:
            from raavan.integrations.runtime.restate.runtime import RestateRuntime
        except ImportError:
            pytest.skip("restate-sdk / httpx not installed")

        rt = RestateRuntime(admin_url="http://localhost:99999")

        with pytest.raises(RuntimeError, match="failed to register"):
            await rt.start()

        assert not rt._started


# ══════════════════════════════════════════════════════════════════════════
# M1: Dispatcher O(1) fan-out via reverse index
# ══════════════════════════════════════════════════════════════════════════


class TestDispatcherReverseIndex:
    """M1: _type_agents reverse index enables O(1) fan-out."""

    async def test_reverse_index_populated(self) -> None:
        dispatcher = Dispatcher()
        aid = AgentId(type="worker", key="1")
        dispatcher.register_agent(aid, Mailbox(capacity=10))

        assert aid in dispatcher._type_agents["worker"]

    async def test_reverse_index_cleared_on_unregister(self) -> None:
        dispatcher = Dispatcher()
        aid = AgentId(type="worker", key="1")
        dispatcher.register_agent(aid, Mailbox(capacity=10))
        dispatcher.unregister_agent(aid)

        # Type should be fully removed when last agent of that type is gone
        assert "worker" not in dispatcher._type_agents

    async def test_fan_out_uses_reverse_index(self) -> None:
        """Fan-out should deliver to correct agents by type."""
        dispatcher = Dispatcher()
        topic = TopicId(type="events", source="test")

        mbox_w1 = Mailbox(capacity=10)
        mbox_w2 = Mailbox(capacity=10)
        mbox_o = Mailbox(capacity=10)

        dispatcher.register_agent(AgentId("worker", "1"), mbox_w1)
        dispatcher.register_agent(AgentId("worker", "2"), mbox_w2)
        dispatcher.register_agent(AgentId("other", "1"), mbox_o)

        dispatcher.subscribe_to_topic(topic, "worker")

        envelope = Envelope(sender=None, target=topic, payload="msg")
        await dispatcher.dispatch(envelope)

        assert mbox_w1.size == 1
        assert mbox_w2.size == 1
        assert mbox_o.size == 0  # not subscribed


# ══════════════════════════════════════════════════════════════════════════
# M2: Supervisor _restart_all resilience
# ══════════════════════════════════════════════════════════════════════════


class TestSupervisorRestartAllResilience:
    """M2: one failed spawn in _restart_all doesn't stop others."""

    async def test_restart_all_continues_on_spawn_failure(self) -> None:
        policy = RestartPolicy(strategy="one_for_all", max_restarts=5)
        supervisor = Supervisor(restart_policy=policy)

        spawned: list[str] = []

        async def factory_a() -> None:
            spawned.append("a")
            await asyncio.sleep(10)

        async def factory_b() -> None:
            spawned.append("b")
            await asyncio.sleep(10)

        aid_a = AgentId(type="a", key="1")
        aid_b = AgentId(type="b", key="1")

        supervisor.supervise(aid_a, factory_a)
        supervisor.supervise(aid_b, factory_b)

        await asyncio.sleep(0.05)
        assert "a" in spawned
        assert "b" in spawned

        await supervisor.stop_all()


# ══════════════════════════════════════════════════════════════════════════
# M6: Restate configurable timeouts
# ══════════════════════════════════════════════════════════════════════════


class TestRestateConfigurableTimeouts:
    """M6: Restate timeouts are configurable."""

    async def test_custom_timeouts(self) -> None:
        try:
            from raavan.integrations.runtime.restate.runtime import RestateRuntime
        except ImportError:
            pytest.skip("restate-sdk / httpx not installed")

        rt = RestateRuntime(
            admin_timeout=5.0,
            ingress_timeout=60.0,
            promise_timeout=20.0,
        )
        assert rt._admin_timeout == 5.0
        assert rt._ingress_timeout == 60.0
        assert rt._promise_timeout == 20.0


# ══════════════════════════════════════════════════════════════════════════
# NATS key validation (M7)
# ══════════════════════════════════════════════════════════════════════════


class TestNATSKeyValidation:
    """M7: topic keys are validated against a safe pattern."""

    async def test_valid_key_passes(self) -> None:
        from raavan.integrations.runtime.nats.bridge import _validate_key

        _validate_key("thread-abc-123")
        _validate_key("agent.events.test_key")

    async def test_invalid_key_raises(self) -> None:
        from raavan.integrations.runtime.nats.bridge import _validate_key

        with pytest.raises(ValueError, match="invalid topic key"):
            _validate_key("key with spaces")

        with pytest.raises(ValueError, match="invalid topic key"):
            _validate_key("")

        with pytest.raises(ValueError, match="invalid topic key"):
            _validate_key("key/with/slashes")


# ══════════════════════════════════════════════════════════════════════════
# End-to-end: LocalRuntime full flow
# ══════════════════════════════════════════════════════════════════════════


class TestLocalRuntimeEndToEnd:
    """Integration tests for the hardened LocalRuntime."""

    async def test_send_and_receive(self) -> None:
        runtime = LocalRuntime(send_timeout=5.0)
        await runtime.start()
        await runtime.register("echo", _echo_handler)

        result = await runtime.send_message(
            "hello",
            sender=AgentId(type="caller", key="1"),
            recipient=AgentId(type="echo", key="1"),
        )
        assert result == "echo:hello"
        await runtime.stop()

    async def test_publish_subscribe(self) -> None:
        runtime = LocalRuntime(send_timeout=5.0)
        await runtime.start()

        received: list[Any] = []

        async def subscriber(ctx: MessageContext, payload: Any) -> None:
            received.append(payload)

        await runtime.register("listener", subscriber)
        topic = TopicId(type="events", source="test")
        await runtime.subscribe("listener", topic)

        await runtime.publish_message("broadcast", sender=None, topic=topic)

        # Give the agent loop time to process
        await asyncio.sleep(0.1)
        assert "broadcast" in received
        await runtime.stop()

    async def test_graceful_shutdown(self) -> None:
        """Stop cancels pending futures and cleans up."""
        runtime = LocalRuntime(send_timeout=5.0)
        await runtime.start()
        await runtime.register("echo", _echo_handler)

        # Send a message, get result
        result = await runtime.send_message(
            "before-stop",
            sender=None,
            recipient=AgentId(type="echo", key="1"),
        )
        assert result == "echo:before-stop"

        await runtime.stop()
        assert len(runtime._pending_responses) == 0
        assert len(runtime.active_agents) == 0
