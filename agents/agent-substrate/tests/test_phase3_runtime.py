"""Phase 3 tests — ToolExecutorHandler, StreamPublisher, runtime dispatch,
integration backend protocol compliance, and end-to-end flows.
"""

from __future__ import annotations

import asyncio
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from raavan.core.runtime import (
    AgentId,
    LocalRuntime,
    StreamDone,
    TopicId,
)
from raavan.core.runtime._stream import StreamPublisher
from raavan.core.runtime._types import MessageContext
from raavan.core.tools.base_tool import BaseTool, HitlMode, ToolResult, ToolRisk
from raavan.catalog.tools._tool_executor import ToolExecutorHandler
from raavan.catalog.tools.human_input.tool import (
    ToolApprovalAction,
    ToolApprovalHandler,
    ToolApprovalResponse,
)


# ── Helpers ──────────────────────────────────────────────────────────────────


class _EchoTool(BaseTool):
    """Simple tool that echoes its input for testing."""

    def __init__(self, name: str = "echo_tool") -> None:
        super().__init__(
            name=name,
            description="Echoes input",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
        )

    async def execute(self, *, text: str = "") -> ToolResult:  # type: ignore[override]
        return ToolResult(
            content=[{"type": "text", "text": f"echo: {text}"}],
            app_data={"echoed": text},
        )


class _FailTool(BaseTool):
    """Tool that always raises for testing error handling."""

    def __init__(self) -> None:
        super().__init__(
            name="fail_tool",
            description="Always fails",
            input_schema={"type": "object", "properties": {}},
        )

    async def execute(self) -> ToolResult:  # type: ignore[override]
        raise RuntimeError("deliberate failure")


class _SlowTool(BaseTool):
    """Tool that sleeps to test timeouts."""

    def __init__(self) -> None:
        super().__init__(
            name="slow_tool",
            description="Sleeps forever",
            input_schema={"type": "object", "properties": {}},
        )

    async def execute(self) -> ToolResult:  # type: ignore[override]
        await asyncio.sleep(999)
        return ToolResult(content="done")  # pragma: no cover


class _CriticalTool(BaseTool):
    """Tool requiring approval."""

    risk = ToolRisk.CRITICAL
    hitl_mode = HitlMode.BLOCKING

    def __init__(self) -> None:
        super().__init__(
            name="critical_tool",
            description="Needs approval",
            input_schema={
                "type": "object",
                "properties": {"action": {"type": "string"}},
            },
        )

    async def execute(self, *, action: str = "") -> ToolResult:  # type: ignore[override]
        return ToolResult(content=[{"type": "text", "text": f"done: {action}"}])


def _make_ctx(agent_id: AgentId | None = None) -> MessageContext:
    """Create a mock MessageContext for handler tests."""
    return MessageContext(
        runtime=MagicMock(),
        sender=AgentId("test", "sender"),
        correlation_id="test-corr-id",
        agent_id=agent_id or AgentId("tool_executor", "thread-1"),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Group 1: ToolExecutorHandler
# ══════════════════════════════════════════════════════════════════════════════


class TestToolExecutorHandler:
    """Test the ToolExecutorHandler message handler."""

    def _handler(self, **kwargs: Any) -> ToolExecutorHandler:
        tools = {
            "echo_tool": _EchoTool(),
            "fail_tool": _FailTool(),
            "slow_tool": _SlowTool(),
            "critical_tool": _CriticalTool(),
        }
        return ToolExecutorHandler(tools=tools, **kwargs)

    async def test_valid_tool_execution(self) -> None:
        handler = self._handler()
        ctx = _make_ctx()
        result = await handler(
            ctx,
            {"tool_name": "echo_tool", "arguments": {"text": "hello"}, "call_id": "c1"},
        )
        assert result["is_error"] is False
        assert result["tool_name"] == "echo_tool"
        assert result["call_id"] == "c1"
        assert result["app_data"] == {"echoed": "hello"}
        assert any("echo: hello" in str(c) for c in result["content"])

    async def test_tool_not_found(self) -> None:
        handler = self._handler()
        ctx = _make_ctx()
        result = await handler(
            ctx,
            {"tool_name": "nonexistent", "arguments": {}, "call_id": "c2"},
        )
        assert result["is_error"] is True
        assert "not found" in str(result["content"])

    async def test_tool_execution_error(self) -> None:
        handler = self._handler()
        ctx = _make_ctx()
        result = await handler(
            ctx,
            {"tool_name": "fail_tool", "arguments": {}, "call_id": "c3"},
        )
        assert result["is_error"] is True
        assert "deliberate failure" in str(result["content"])

    async def test_tool_timeout(self) -> None:
        handler = self._handler(tool_timeout=0.05)
        ctx = _make_ctx()
        result = await handler(
            ctx,
            {"tool_name": "slow_tool", "arguments": {}, "call_id": "c4"},
        )
        assert result["is_error"] is True
        assert "timed out" in str(result["content"])

    async def test_invalid_payload(self) -> None:
        handler = self._handler()
        ctx = _make_ctx()
        result = await handler(ctx, "not a dict")
        assert result["is_error"] is True
        assert "Invalid payload" in str(result["content"])

    async def test_hitl_approval_deny(self) -> None:
        """HITL approval handler denies → error response."""
        mock_handler = AsyncMock(spec=ToolApprovalHandler)
        mock_handler.request_approval.return_value = ToolApprovalResponse(
            request_id="req-1",
            action=ToolApprovalAction.DENY,
            reason="user said no",
        )
        handler = self._handler(
            tool_approval_handler=mock_handler,
            tools_requiring_approval=["critical_tool"],
        )
        ctx = _make_ctx()
        result = await handler(
            ctx,
            {
                "tool_name": "critical_tool",
                "arguments": {"action": "delete"},
                "call_id": "c5",
            },
        )
        assert result["is_error"] is True
        assert "denied" in str(result["content"]).lower()

    async def test_hitl_approval_approve(self) -> None:
        """HITL approval handler approves → tool executes."""
        mock_handler = AsyncMock(spec=ToolApprovalHandler)
        mock_handler.request_approval.return_value = ToolApprovalResponse(
            request_id="req-2",
            action=ToolApprovalAction.APPROVE,
        )
        handler = self._handler(
            tool_approval_handler=mock_handler,
            tools_requiring_approval=["critical_tool"],
        )
        ctx = _make_ctx()
        result = await handler(
            ctx,
            {
                "tool_name": "critical_tool",
                "arguments": {"action": "send"},
                "call_id": "c6",
            },
        )
        assert result["is_error"] is False
        assert "done: send" in str(result["content"])

    async def test_hitl_approval_modify(self) -> None:
        """HITL approval handler modifies arguments → tool uses modified args."""
        mock_handler = AsyncMock(spec=ToolApprovalHandler)
        mock_handler.request_approval.return_value = ToolApprovalResponse(
            request_id="req-3",
            action=ToolApprovalAction.MODIFY,
            modified_arguments={"action": "modified_action"},
        )
        handler = self._handler(
            tool_approval_handler=mock_handler,
            tools_requiring_approval=["critical_tool"],
        )
        ctx = _make_ctx()
        result = await handler(
            ctx,
            {
                "tool_name": "critical_tool",
                "arguments": {"action": "original"},
                "call_id": "c7",
            },
        )
        assert result["is_error"] is False
        assert "done: modified_action" in str(result["content"])


# ══════════════════════════════════════════════════════════════════════════════
# Group 2: StreamPublisher + StreamDone
# ══════════════════════════════════════════════════════════════════════════════


class TestStreamDone:
    """Test StreamDone sentinel dataclass."""

    def test_default_reason(self) -> None:
        done = StreamDone()
        assert done.reason == "complete"

    def test_custom_reason(self) -> None:
        done = StreamDone(reason="cancelled")
        assert done.reason == "cancelled"

    def test_frozen(self) -> None:
        done = StreamDone()
        with pytest.raises(AttributeError):
            done.reason = "changed"  # type: ignore[misc]


class TestStreamPublisher:
    """Test StreamPublisher emit/close lifecycle."""

    async def test_emit_publishes_to_topic(self) -> None:
        runtime = AsyncMock()
        topic = TopicId(type="stream", source="thread-1")
        sender = AgentId("agent", "1")
        pub = StreamPublisher(runtime, topic, sender=sender)

        await pub.emit({"type": "text_delta", "content": "hi"})

        runtime.publish_message.assert_called_once_with(
            {"type": "text_delta", "content": "hi"},
            sender=sender,
            topic=topic,
        )

    async def test_close_sends_stream_done(self) -> None:
        runtime = AsyncMock()
        topic = TopicId(type="stream", source="thread-1")
        sender = AgentId("agent", "1")
        pub = StreamPublisher(runtime, topic, sender=sender)

        await pub.close()

        call_args = runtime.publish_message.call_args
        payload = call_args[0][0]
        assert isinstance(payload, StreamDone)
        assert payload.reason == "complete"

    async def test_close_custom_reason(self) -> None:
        runtime = AsyncMock()
        pub = StreamPublisher(
            runtime,
            TopicId(type="stream", source="t"),
            sender=AgentId("a", "1"),
        )
        await pub.close("error")
        payload = runtime.publish_message.call_args[0][0]
        assert isinstance(payload, StreamDone)
        assert payload.reason == "error"

    async def test_emit_after_close_raises(self) -> None:
        runtime = AsyncMock()
        pub = StreamPublisher(
            runtime,
            TopicId(type="stream", source="t"),
            sender=AgentId("a", "1"),
        )
        await pub.close()
        with pytest.raises(RuntimeError, match="closed"):
            await pub.emit("data")

    async def test_double_close_is_idempotent(self) -> None:
        runtime = AsyncMock()
        pub = StreamPublisher(
            runtime,
            TopicId(type="stream", source="t"),
            sender=AgentId("a", "1"),
        )
        await pub.close()
        await pub.close()  # Should not raise
        assert runtime.publish_message.call_count == 1

    async def test_topic_property(self) -> None:
        runtime = AsyncMock()
        topic = TopicId(type="stream", source="xyz")
        pub = StreamPublisher(runtime, topic, sender=AgentId("a", "1"))
        assert pub.topic == topic


# ══════════════════════════════════════════════════════════════════════════════
# Group 3: Runtime-wired tool dispatch via LocalRuntime
# ══════════════════════════════════════════════════════════════════════════════


class TestToolExecutorViaRuntime:
    """End-to-end: ToolExecutorHandler registered on LocalRuntime,
    dispatched via send_message."""

    async def test_dispatch_tool_via_runtime(self) -> None:
        """Agent dispatches tool call through runtime → ToolExecutorHandler."""
        rt = LocalRuntime()
        await rt.start()

        echo = _EchoTool()
        handler = ToolExecutorHandler(tools={"echo_tool": echo})
        await rt.register("tool_executor", handler)

        # Simulate what _execute_tool_via_runtime does
        response = await rt.send_message(
            {"tool_name": "echo_tool", "arguments": {"text": "world"}, "call_id": "x1"},
            sender=AgentId("chat_agent", "thread-1"),
            recipient=AgentId("tool_executor", "thread-1"),
        )

        assert response["is_error"] is False
        assert response["tool_name"] == "echo_tool"
        assert "echo: world" in str(response["content"])
        await rt.stop()

    async def test_dispatch_unknown_tool(self) -> None:
        """Tool not found → error response via runtime."""
        rt = LocalRuntime()
        await rt.start()

        handler = ToolExecutorHandler(tools={"echo_tool": _EchoTool()})
        await rt.register("tool_executor", handler)

        response = await rt.send_message(
            {"tool_name": "missing", "arguments": {}, "call_id": "x2"},
            sender=AgentId("chat_agent", "t1"),
            recipient=AgentId("tool_executor", "t1"),
        )

        assert response["is_error"] is True
        assert "not found" in str(response["content"])
        await rt.stop()

    async def test_tool_failure_via_runtime(self) -> None:
        """Tool raises → error response via runtime."""
        rt = LocalRuntime()
        await rt.start()

        handler = ToolExecutorHandler(tools={"fail_tool": _FailTool()})
        await rt.register("tool_executor", handler)

        response = await rt.send_message(
            {"tool_name": "fail_tool", "arguments": {}, "call_id": "x3"},
            sender=AgentId("chat_agent", "t1"),
            recipient=AgentId("tool_executor", "t1"),
        )

        assert response["is_error"] is True
        assert "deliberate failure" in str(response["content"])
        await rt.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Group 4: Stream pub/sub via LocalRuntime
# ══════════════════════════════════════════════════════════════════════════════


class TestStreamPubSubViaRuntime:
    """Test TopicId pub/sub with StreamPublisher on LocalRuntime."""

    async def test_subscribe_receives_events(self) -> None:
        """Subscriber receives all published events including StreamDone."""
        rt = LocalRuntime()
        await rt.start()

        received: List[Any] = []

        async def subscriber(ctx: MessageContext, payload: Any) -> Any:
            received.append(payload)
            return None

        await rt.register("stream_consumer", subscriber)
        topic = TopicId(type="stream", source="thread-1")
        await rt.subscribe("stream_consumer", topic)

        publisher = StreamPublisher(rt, topic, sender=AgentId("chat_agent", "thread-1"))

        await publisher.emit({"type": "text_delta", "content": "hello"})
        await publisher.emit({"type": "text_delta", "content": "world"})
        await publisher.close()

        # Give the event loop time to drain mailbox-backed handlers
        await asyncio.sleep(0.1)

        assert len(received) == 3
        assert received[0] == {"type": "text_delta", "content": "hello"}
        assert received[1] == {"type": "text_delta", "content": "world"}
        assert isinstance(received[2], StreamDone)
        assert received[2].reason == "complete"
        await rt.stop()


# ══════════════════════════════════════════════════════════════════════════════
# Group 5: Integration backend protocol compliance (mock-based)
# ══════════════════════════════════════════════════════════════════════════════


class TestBaseRemoteRuntime:
    """BaseRemoteRuntime ABC provides shared local dispatch for all remote backends."""

    async def test_grpc_inherits_base(self) -> None:
        from raavan.integrations.runtime._base import BaseRemoteRuntime
        from raavan.integrations.runtime.grpc import GrpcRuntime

        if GrpcRuntime.__bases__[0] is not BaseRemoteRuntime:
            pytest.skip("grpcio not installed")
        try:
            rt = GrpcRuntime()
        except ImportError:
            pytest.skip("grpcio not installed")
        assert isinstance(rt, BaseRemoteRuntime)

    async def test_restate_inherits_base(self) -> None:
        from raavan.integrations.runtime._base import BaseRemoteRuntime
        from raavan.integrations.runtime.restate import RestateRuntime

        try:
            rt = RestateRuntime()
        except ImportError:
            pytest.skip("restate-sdk not installed")
        assert isinstance(rt, BaseRemoteRuntime)

    async def test_base_register_and_dispatch(self) -> None:
        """BaseRemoteRuntime.register + send_message local dispatch works."""
        from raavan.integrations.runtime.restate import RestateRuntime

        try:
            rt = RestateRuntime()
        except ImportError:
            pytest.skip("restate-sdk not installed")

        received: List[Any] = []

        async def handler(ctx: MessageContext, payload: Any) -> Any:
            received.append(payload)
            return "base-ok"

        await rt.register("agent_a", handler)
        rt._started = True  # bypass lifecycle guard (no Restate admin in tests)
        result = await rt.send_message(
            "hello",
            sender=AgentId("x", "1"),
            recipient=AgentId("agent_a", "instance-1"),
        )
        assert result == "base-ok"
        assert received == ["hello"]

    async def test_base_subscribe_and_publish(self) -> None:
        """BaseRemoteRuntime pub/sub local fan-out works."""
        from raavan.integrations.runtime.restate import RestateRuntime

        try:
            rt = RestateRuntime()
        except ImportError:
            pytest.skip("restate-sdk not installed")

        received: List[Any] = []

        async def handler(ctx: MessageContext, payload: Any) -> Any:
            received.append(payload)
            return None

        topic = TopicId(type="events", source="s1")
        await rt.register("listener", handler)
        await rt.subscribe("listener", topic)
        rt._started = True  # bypass lifecycle guard (no Restate admin in tests)
        await rt.publish_message("event1", sender=AgentId("pub", "1"), topic=topic)
        await rt.publish_message("event2", sender=AgentId("pub", "1"), topic=topic)

        assert received == ["event1", "event2"]

    async def test_subscribe_unknown_type_raises(self) -> None:
        """Subscribing an unregistered agent type raises ValueError."""
        from raavan.integrations.runtime.restate import RestateRuntime

        try:
            rt = RestateRuntime()
        except ImportError:
            pytest.skip("restate-sdk not installed")

        with pytest.raises(ValueError, match="unknown agent type"):
            await rt.subscribe("not_registered", TopicId(type="t", source="s"))


class TestGrpcRuntimeProtocol:
    """GrpcRuntime implements the AgentRuntime interface."""

    def test_import_without_grpc(self) -> None:
        """When grpcio is not installed, import raises ImportError."""
        with patch.dict("sys.modules", {"grpc": None, "grpc.aio": None}):
            # Re-import to trigger the guard
            import importlib
            import sys

            mod_name = "raavan.integrations.runtime.grpc.runtime"
            if mod_name in sys.modules:
                del sys.modules[mod_name]

            # The module-level try/except sets _HAS_GRPC = False
            # Constructor raises ImportError
            mod = importlib.import_module(mod_name)
            if not mod._HAS_GRPC:
                with pytest.raises(ImportError, match="grpcio"):
                    mod.GrpcRuntime()

    async def test_local_handler_dispatch(self) -> None:
        """GrpcRuntime dispatches to locally registered handlers."""
        try:
            from raavan.integrations.runtime.grpc import GrpcRuntime
        except ImportError:
            pytest.skip("grpcio not installed")

        rt = GrpcRuntime()
        calls: List[Any] = []

        async def handler(ctx: MessageContext, payload: Any) -> Any:
            calls.append(payload)
            return {"result": "ok"}

        await rt.register("test_agent", handler)
        await rt.start()

        response = await rt.send_message(
            {"action": "test"},
            sender=AgentId("caller", "1"),
            recipient=AgentId("test_agent", "abc"),
        )

        assert response == {"result": "ok"}
        assert len(calls) == 1
        await rt.stop()


class TestRestateRuntimeProtocol:
    """RestateRuntime implements the AgentRuntime interface."""

    async def test_local_handler_dispatch(self) -> None:
        """RestateRuntime dispatches to locally registered handlers."""
        try:
            from raavan.integrations.runtime.restate import RestateRuntime
        except ImportError:
            pytest.skip("restate-sdk not installed")

        rt = RestateRuntime()
        calls: List[Any] = []

        async def handler(ctx: MessageContext, payload: Any) -> Any:
            calls.append(payload)
            return {"status": "done"}

        await rt.register("my_agent", handler)
        rt._started = True  # bypass lifecycle guard (no Restate admin in tests)

        response = await rt.send_message(
            {"task": "process"},
            sender=AgentId("sender", "1"),
            recipient=AgentId("my_agent", "key-1"),
        )

        assert response == {"status": "done"}
        assert len(calls) == 1


class TestNATSBridgeProtocol:
    """NATSBridge smoke test — import only (no real NATS server)."""

    def test_import_without_nats(self) -> None:
        """When nats-py is not installed, constructor raises ImportError."""
        with patch.dict("sys.modules", {"nats": None}):
            import importlib
            import sys

            mod_name = "raavan.integrations.runtime.nats.bridge"
            if mod_name in sys.modules:
                del sys.modules[mod_name]

            mod = importlib.import_module(mod_name)
            if not mod._HAS_NATS:
                with pytest.raises(ImportError, match="nats-py"):
                    mod.NATSBridge()


# ══════════════════════════════════════════════════════════════════════════════
# Group 6: Backward compatibility
# ══════════════════════════════════════════════════════════════════════════════


class TestBackwardCompat:
    """Agents without runtime still work identically."""

    async def test_agent_without_runtime_has_none(self) -> None:
        """BaseAgent defaults to runtime=None, agent_id=None."""
        from typing import AsyncIterator as _AsyncIterator

        from raavan.core.agents.base_agent import BaseAgent

        class _DummyAgent(BaseAgent):
            async def run(self, input_text: str, **kw: Any) -> Any:
                return "ok"

            async def run_stream(
                self, input_text: str, **kw: Any
            ) -> _AsyncIterator[Any]:
                yield "ok"  # type: ignore[misc]

        agent = _DummyAgent(
            name="test",
            description="test agent",
            model_client=MagicMock(),
            model_context=MagicMock(),
            tools=[],
        )
        assert agent.runtime is None
        assert agent.agent_id is None

    async def test_server_context_runtime_optional(self) -> None:
        """ServerContext.runtime defaults to None."""
        from raavan.server.context import ServerContext

        ctx = ServerContext(
            model_client=MagicMock(),
            redis_memory=MagicMock(),
            tools=MagicMock(),
            bridge_registry=MagicMock(),
            tools_requiring_approval=[],
            system_instructions="test",
            tool_timeout=30.0,
        )
        assert ctx.runtime is None

    async def test_server_context_with_runtime(self) -> None:
        """ServerContext accepts runtime kwarg."""
        from raavan.server.context import ServerContext

        runtime = LocalRuntime()
        ctx = ServerContext(
            model_client=MagicMock(),
            redis_memory=MagicMock(),
            tools=MagicMock(),
            bridge_registry=MagicMock(),
            tools_requiring_approval=[],
            system_instructions="test",
            tool_timeout=30.0,
            runtime=runtime,
        )
        assert ctx.runtime is runtime
