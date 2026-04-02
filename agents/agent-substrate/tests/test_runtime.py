"""Tests for core/runtime/ — actor-based agent runtime primitives."""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from raavan.core.agents.agent_result import AgentRunResult, RunStatus
from raavan.core.agents.base_agent import BaseAgent
from raavan.core.runtime import (
    AgentId,
    AgentNotFoundError,
    AgentRuntime,
    Dispatcher,
    Envelope,
    LocalRuntime,
    Mailbox,
    MailboxFullError,
    MessageContext,
    RestartPolicy,
    Supervisor,
    SupervisorEscalation,
    TopicId,
)


# ===================================================================
# Identity types
# ===================================================================


class TestAgentId:
    def test_frozen_and_hashable(self) -> None:
        a = AgentId("react", "t1")
        b = AgentId("react", "t1")
        assert a == b
        assert hash(a) == hash(b)
        assert {a, b} == {a}

    def test_str(self) -> None:
        assert str(AgentId("react", "t1")) == "react/t1"

    def test_immutable(self) -> None:
        a = AgentId("react", "t1")
        with pytest.raises(AttributeError):
            a.type = "other"  # type: ignore[misc]


class TestTopicId:
    def test_frozen_and_hashable(self) -> None:
        t = TopicId("sse", "thread-1")
        assert str(t) == "sse/thread-1"

    def test_equality(self) -> None:
        assert TopicId("a", "b") == TopicId("a", "b")
        assert TopicId("a", "b") != TopicId("a", "c")


# ===================================================================
# Mailbox
# ===================================================================


class TestMailbox:
    async def test_put_and_get(self) -> None:
        mb = Mailbox(capacity=10)
        env = Envelope(sender=None, target=AgentId("x", "1"), payload="hello")
        await mb.put(env)
        got = await mb.get(timeout=1.0)
        assert got.payload == "hello"

    async def test_size_tracking(self) -> None:
        mb = Mailbox(capacity=5)
        assert mb.is_empty
        assert not mb.is_full
        for i in range(5):
            env = Envelope(sender=None, target=AgentId("x", "1"), payload=i)
            await mb.put(env)
        assert mb.size == 5
        assert mb.is_full

    async def test_put_nowait_full_raises(self) -> None:
        mb = Mailbox(capacity=1)
        env = Envelope(sender=None, target=AgentId("x", "1"), payload="a")
        mb.put_nowait(env)
        with pytest.raises(MailboxFullError):
            mb.put_nowait(env)

    async def test_get_timeout(self) -> None:
        mb = Mailbox(capacity=10)
        with pytest.raises(asyncio.TimeoutError):
            await mb.get(timeout=0.05)

    async def test_close_stops_get(self) -> None:
        mb = Mailbox(capacity=10)
        mb.close()
        assert mb.closed
        with pytest.raises(StopAsyncIteration):
            await mb.get(timeout=0.1)

    async def test_put_after_close_raises(self) -> None:
        mb = Mailbox(capacity=10)
        mb.close()
        env = Envelope(sender=None, target=AgentId("x", "1"), payload="a")
        with pytest.raises(MailboxFullError):
            await mb.put(env)


# ===================================================================
# Dispatcher
# ===================================================================


class TestDispatcher:
    async def test_register_and_dispatch_to_agent(self) -> None:
        d = Dispatcher()
        aid = AgentId("worker", "1")
        mb = Mailbox()
        d.register_agent(aid, mb)

        env = Envelope(sender=None, target=aid, payload="task")
        await d.dispatch(env)
        got = await mb.get(timeout=1.0)
        assert got.payload == "task"

    async def test_dispatch_to_unknown_agent_raises(self) -> None:
        d = Dispatcher()
        env = Envelope(
            sender=None,
            target=AgentId("ghost", "1"),
            payload="nope",
        )
        with pytest.raises(AgentNotFoundError):
            await d.dispatch(env)

    async def test_unregister_agent(self) -> None:
        d = Dispatcher()
        aid = AgentId("worker", "1")
        d.register_agent(aid, Mailbox())
        d.unregister_agent(aid)
        assert aid not in d.registered_agents

    async def test_topic_fanout(self) -> None:
        d = Dispatcher()
        topic = TopicId("events", "session-1")

        mb_a = Mailbox()
        mb_b = Mailbox()
        aid_a = AgentId("listener", "a")
        aid_b = AgentId("listener2", "b")
        d.register_agent(aid_a, mb_a)
        d.register_agent(aid_b, mb_b)

        d.subscribe_to_topic(topic, "listener")
        d.subscribe_to_topic(topic, "listener2")

        env = Envelope(sender=None, target=topic, payload="broadcast")
        await d.dispatch(env)

        got_a = await mb_a.get(timeout=1.0)
        got_b = await mb_b.get(timeout=1.0)
        assert got_a.payload == "broadcast"
        assert got_b.payload == "broadcast"

    async def test_unsubscribe(self) -> None:
        d = Dispatcher()
        topic = TopicId("events", "s1")
        sub = d.subscribe_to_topic(topic, "worker")
        d.unsubscribe(sub.id)
        # Should no longer have subscriptions for this topic
        assert len(d._topic_subscribers.get(topic, [])) == 0

    async def test_get_mailbox(self) -> None:
        d = Dispatcher()
        aid = AgentId("worker", "1")
        mb = Mailbox()
        d.register_agent(aid, mb)
        assert d.get_mailbox(aid) is mb
        assert d.get_mailbox(AgentId("ghost", "1")) is None


# ===================================================================
# Supervisor
# ===================================================================


class TestSupervisor:
    async def test_restart_on_crash(self) -> None:
        """Agent that crashes once should be restarted."""
        call_count = 0

        async def flaky_agent() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("boom")
            # Second run succeeds — just wait to be cancelled
            await asyncio.sleep(10)

        policy = RestartPolicy(max_restarts=3, restart_window=5.0)
        sup = Supervisor(policy)
        aid = AgentId("flaky", "1")
        sup.supervise(aid, flaky_agent)

        # Give the supervisor time to restart
        await asyncio.sleep(0.2)
        assert call_count >= 2

        await sup.stop_all()

    async def test_restart_budget_exceeded(self) -> None:
        """Exceeding the restart budget should raise SupervisorEscalation."""

        async def always_crash() -> None:
            raise RuntimeError("always fails")

        policy = RestartPolicy(max_restarts=2, restart_window=10.0)
        sup = Supervisor(policy)
        aid = AgentId("crasher", "1")
        sup.supervise(aid, always_crash)

        # Give supervisor time to exhaust the restart budget
        await asyncio.sleep(0.3)

        # The final re-spawned task holds the SupervisorEscalation
        final_task = sup._tasks.get(aid)
        assert final_task is not None and final_task.done()
        assert isinstance(final_task.exception(), SupervisorEscalation)

        await sup.stop_all()

    async def test_stop_all_cancels_tasks(self) -> None:
        cancelled = False

        async def long_running() -> None:
            nonlocal cancelled
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled = True
                raise

        sup = Supervisor()
        sup.supervise(AgentId("long", "1"), long_running)
        await asyncio.sleep(0.05)

        await sup.stop_all()
        assert cancelled
        assert len(sup.supervised_agents) == 0


# ===================================================================
# LocalRuntime
# ===================================================================


class TestLocalRuntime:
    async def test_protocol_compliance(self) -> None:
        """LocalRuntime must satisfy the AgentRuntime protocol."""
        rt = LocalRuntime()
        assert isinstance(rt, AgentRuntime)

    async def test_register_and_send_message(self) -> None:
        """Basic request-response via send_message."""

        async def echo_handler(ctx: MessageContext, payload: str) -> str:
            return f"echo:{payload}"

        rt = LocalRuntime()
        await rt.start()
        await rt.register("echo", echo_handler)

        result = await rt.send_message(
            "hello",
            sender=AgentId("caller", "c1"),
            recipient=AgentId("echo", "instance-1"),
        )
        assert result == "echo:hello"

        await rt.stop()

    async def test_lazy_agent_creation(self) -> None:
        """Agents should be created only on first message, not at register time."""
        created = False

        async def handler(ctx: MessageContext, payload: str) -> str:
            nonlocal created
            created = True
            return "ok"

        rt = LocalRuntime()
        await rt.start()
        await rt.register("lazy", handler)

        # Not created yet
        assert not created
        assert len(rt.active_agents) == 0

        # Sending a message triggers creation
        await rt.send_message("go", recipient=AgentId("lazy", "1"))
        assert created
        assert AgentId("lazy", "1") in rt.active_agents

        await rt.stop()

    async def test_publish_message_fanout(self) -> None:
        """publish_message should deliver to all topic subscribers."""
        received: list[str] = []

        async def listener(ctx: MessageContext, payload: str) -> None:
            received.append(f"{ctx.agent_id}:{payload}")

        rt = LocalRuntime()
        await rt.start()

        topic = TopicId("notifications", "session-1")
        await rt.register("listener", listener)
        await rt.subscribe("listener", topic)

        await rt.publish_message(
            "alert!",
            sender=AgentId("system", "0"),
            topic=topic,
        )

        # Give the agent loop time to process
        await asyncio.sleep(0.1)
        assert len(received) == 1
        assert "alert!" in received[0]

        await rt.stop()

    async def test_send_to_unknown_type_raises(self) -> None:
        """Sending to an unregistered agent type should raise."""
        rt = LocalRuntime()
        await rt.start()

        with pytest.raises(AgentNotFoundError):
            await rt.send_message(
                "nope",
                recipient=AgentId("nonexistent", "1"),
            )

        await rt.stop()

    async def test_subscribe_unknown_type_raises(self) -> None:
        rt = LocalRuntime()
        with pytest.raises(ValueError, match="unknown agent type"):
            await rt.subscribe("nonexistent", TopicId("t", "s"))

    async def test_stop_cleans_up(self) -> None:
        async def handler(ctx: MessageContext, payload: str) -> str:
            return "ok"

        rt = LocalRuntime()
        await rt.start()
        await rt.register("worker", handler)
        await rt.send_message("ping", recipient=AgentId("worker", "1"))
        assert len(rt.active_agents) == 1

        await rt.stop()
        assert len(rt.active_agents) == 0


# ===================================================================
# Integration: ping-pong between two agents
# ===================================================================


class TestPingPong:
    async def test_two_agents_exchange_messages(self) -> None:
        """Two agents communicate point-to-point via send_message."""
        log: list[str] = []

        async def ping_handler(ctx: MessageContext, payload: str) -> str:
            log.append(f"ping received: {payload}")
            if payload == "start":
                # Send to pong and return its response
                resp = await ctx.runtime.send_message(
                    "ping!",
                    sender=ctx.agent_id,
                    recipient=AgentId("pong", "game-1"),
                )
                return f"pong said: {resp}"
            return "unexpected"

        async def pong_handler(ctx: MessageContext, payload: str) -> str:
            log.append(f"pong received: {payload}")
            return "pong!"

        rt = LocalRuntime()
        await rt.start()
        await rt.register("ping", ping_handler)
        await rt.register("pong", pong_handler)

        result = await rt.send_message(
            "start",
            sender=AgentId("test", "harness"),
            recipient=AgentId("ping", "game-1"),
        )

        assert result == "pong said: pong!"
        assert "ping received: start" in log
        assert "pong received: ping!" in log

        await rt.stop()


# ===================================================================
# Envelope
# ===================================================================


class TestEnvelope:
    def test_auto_fields(self) -> None:
        env = Envelope(
            sender=AgentId("a", "1"),
            target=AgentId("b", "2"),
            payload={"key": "val"},
        )
        assert env.correlation_id  # auto-generated UUID hex
        assert env.created_at is not None
        assert env.metadata == {}

    def test_custom_correlation_id(self) -> None:
        env = Envelope(
            sender=None,
            target=TopicId("t", "s"),
            payload="x",
            correlation_id="custom-123",
        )
        assert env.correlation_id == "custom-123"


# ===================================================================
# Phase 2 — Agent ↔ Runtime integration
# ===================================================================


# -- Minimal concrete agent for testing ------------------------------------


class _StubAgent(BaseAgent):
    """Concrete BaseAgent subclass that returns canned responses."""

    def __init__(self, name: str = "stub", output: str = "hello", **kwargs: Any):
        # Provide minimal required args with mocks
        mc = MagicMock()  # model_client
        ctx = MagicMock()  # model_context
        super().__init__(
            name=name,
            description=f"Stub agent: {name}",
            model_client=mc,
            model_context=ctx,
            **kwargs,
        )
        self._output = output

    async def run(self, input_text: str, **kwargs: Any) -> AgentRunResult:
        return AgentRunResult(
            agent_name=self.name,
            output=[self._output],
            status=RunStatus.COMPLETED,
        )

    async def run_stream(self, input_text: str, **kwargs: Any) -> AsyncIterator[Any]:
        yield {"type": "text_delta", "content": self._output}


class TestBaseAgentRuntime:
    """BaseAgent stores runtime and agent_id when provided."""

    def test_defaults_are_none(self) -> None:
        agent = _StubAgent()
        assert agent.runtime is None
        assert agent.agent_id is None

    def test_stores_runtime_and_id(self) -> None:
        rt = MagicMock()
        aid = AgentId("stub", "k1")
        agent = _StubAgent(runtime=rt, agent_id=aid)
        assert agent.runtime is rt
        assert agent.agent_id == aid


class TestHandleMessage:
    """handle_message() adapts any BaseAgent into a MessageHandler."""

    async def test_calls_run_and_returns_output(self) -> None:
        agent = _StubAgent(output="reply-42")
        ctx = MessageContext(
            runtime=MagicMock(),
            sender=AgentId("caller", "1"),
            correlation_id="corr",
            agent_id=AgentId("stub", "1"),
        )
        result = await agent.handle_message(ctx, "What is 6*7?")
        assert result == ["reply-42"]

    async def test_payload_is_stringified(self) -> None:
        """Non-string payloads are str()-ified before reaching run()."""
        calls: list[str] = []

        class CapturingAgent(_StubAgent):
            async def run(self, input_text: str, **kw: Any) -> AgentRunResult:
                calls.append(input_text)
                return await super().run(input_text, **kw)

        agent = CapturingAgent()
        ctx = MessageContext(
            runtime=MagicMock(),
            sender=None,
            correlation_id="c",
            agent_id=AgentId("cap", "1"),
        )
        await agent.handle_message(ctx, {"complex": True})
        assert calls == ["{'complex': True}"]

    async def test_registered_on_local_runtime(self) -> None:
        """An agent with handle_message can be registered on LocalRuntime."""
        rt = LocalRuntime()
        await rt.start()

        agent = _StubAgent(
            name="echo",
            output="pong",
            runtime=rt,
            agent_id=AgentId("echo", "1"),
        )
        await rt.register("echo", agent.handle_message)
        result = await rt.send_message(
            "ping",
            sender=None,
            recipient=AgentId("echo", "1"),
        )
        assert result == ["pong"]
        await rt.stop()


class TestHandoffToolDualMode:
    """_HandoffTool dispatches via runtime or falls back to agent.run()."""

    async def test_fallback_without_runtime(self) -> None:
        """Without runtime, _HandoffTool calls agent.run() directly."""
        from raavan.core.agents.orchestrator_agent import _HandoffTool

        agent = _StubAgent(output="direct-result")
        tool = _HandoffTool(agent, runtime=None)
        result = await tool.execute(input="do something")
        assert result.content[0]["text"] == "direct-result"

    async def test_dispatch_via_runtime(self) -> None:
        """With runtime + agent_id, _HandoffTool uses runtime.send_message()."""
        from raavan.core.agents.orchestrator_agent import _HandoffTool

        agent = _StubAgent(
            output="ignored",
            agent_id=AgentId("sub", "1"),
        )
        mock_runtime = AsyncMock()
        mock_runtime.send_message = AsyncMock(return_value="runtime-result")

        tool = _HandoffTool(agent, runtime=mock_runtime)
        result = await tool.execute(input="route this")

        mock_runtime.send_message.assert_awaited_once_with(
            "route this",
            sender=None,
            recipient=AgentId("sub", "1"),
        )
        assert result.content[0]["text"] == "runtime-result"

    async def test_fallback_when_agent_has_no_id(self) -> None:
        """Even with runtime, falls back if agent has no agent_id."""
        from raavan.core.agents.orchestrator_agent import _HandoffTool

        agent = _StubAgent(output="fallback-ok")  # no agent_id
        mock_runtime = AsyncMock()

        tool = _HandoffTool(agent, runtime=mock_runtime)
        result = await tool.execute(input="test")

        mock_runtime.send_message.assert_not_awaited()
        assert result.content[0]["text"] == "fallback-ok"


class TestOrchestratorRuntimeIntegration:
    """OrchestratorAgent end-to-end with LocalRuntime."""

    async def test_sub_agent_via_runtime(self) -> None:
        """OrchestratorAgent's _HandoffTool dispatches through runtime."""
        rt = LocalRuntime()
        await rt.start()

        sub = _StubAgent(
            name="specialist",
            output="specialist-answer",
            runtime=rt,
            agent_id=AgentId("specialist", "1"),
        )
        await rt.register("specialist", sub.handle_message)

        # We can't instantiate OrchestratorAgent without a real LLM call,
        # but we can verify the _HandoffTool it creates works correctly.
        from raavan.core.agents.orchestrator_agent import _HandoffTool

        tool = _HandoffTool(sub, runtime=rt)
        result = await tool.execute(input="solve this")
        assert result.content[0]["text"] == "specialist-answer"
        await rt.stop()

    async def test_orchestrator_stores_runtime(self) -> None:
        """OrchestratorAgent passes runtime to super and handoff tools."""
        from raavan.core.agents.orchestrator_agent import OrchestratorAgent

        rt = MagicMock()
        sub = _StubAgent(name="worker", output="ok")
        mc = MagicMock()  # model_client

        orch = OrchestratorAgent(
            name="orch",
            description="test orchestrator",
            model_client=mc,
            sub_agents=[sub],
            runtime=rt,
            agent_id=AgentId("orch", "1"),
        )
        assert orch.runtime is rt
        assert orch.agent_id == AgentId("orch", "1")

        # Verify handoff tools got the runtime
        for tool in orch._handoff_tools.values():
            assert tool._runtime is rt


class TestDistributedPackage:
    """The distributed/ package provides durable agent execution."""

    def test_import_succeeds(self) -> None:
        import importlib

        mod = importlib.import_module("raavan.distributed")
        assert hasattr(mod, "NATSStreamingBridge")
        assert hasattr(mod, "RestateClient")
        assert hasattr(mod, "ToolPolicy")
