"""Tests for the distributed durable execution package.

Covers: ToolPolicy, NATSStreamingBridge, activities, RestateClient.
All external dependencies (NATS, Restate, Redis, LLM) are mocked.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from raavan.distributed.policies import (
    TOOL_POLICIES,
    ToolPolicy,
    derive_policy_from_tool,
    get_policy,
)


# ═══════════════════════════════════════════════════════════════════════
# ToolPolicy
# ═══════════════════════════════════════════════════════════════════════


class TestToolPolicy:
    def test_default_values(self) -> None:
        p = ToolPolicy()
        assert p.timeout == 30.0
        assert p.needs_idempotency is False
        assert p.requires_approval is False
        assert p.is_hitl_input is False
        assert p.large_payload is False
        assert p.max_retries == 1

    def test_frozen(self) -> None:
        p = ToolPolicy()
        with pytest.raises(AttributeError):
            p.timeout = 99  # type: ignore[misc]

    def test_known_tool_lookup(self) -> None:
        p = TOOL_POLICIES["send_email"]
        assert p.needs_idempotency is True
        assert p.requires_approval is True

    def test_ask_human_is_hitl(self) -> None:
        p = TOOL_POLICIES["ask_human"]
        assert p.is_hitl_input is True
        assert p.timeout == 300.0

    def test_get_policy_known(self) -> None:
        p = get_policy("web_surfer")
        assert p.timeout == 120.0
        assert p.max_retries == 2

    def test_get_policy_unknown(self) -> None:
        p = get_policy("unknown_tool_xyz")
        assert p == ToolPolicy()

    def test_derive_from_safe_tool(self) -> None:
        tool = MagicMock()
        tool.name = "safe_calc"
        tool.risk = "safe"
        tool.hitl_mode = "blocking"
        tool.hitl_timeout_seconds = None

        from raavan.core.tools.base_tool import HitlMode, ToolRisk

        tool.risk = ToolRisk.SAFE
        tool.hitl_mode = HitlMode.BLOCKING

        p = derive_policy_from_tool(tool)
        assert p.needs_idempotency is False
        assert p.requires_approval is False

    def test_derive_from_critical_tool(self) -> None:
        from raavan.core.tools.base_tool import HitlMode, ToolRisk

        tool = MagicMock()
        tool.name = "nuclear_launch"
        tool.risk = ToolRisk.CRITICAL
        tool.hitl_mode = HitlMode.BLOCKING
        tool.hitl_timeout_seconds = 60.0

        p = derive_policy_from_tool(tool)
        assert p.needs_idempotency is True
        assert p.requires_approval is True
        assert p.timeout == 60.0

    def test_derive_falls_back_to_table(self) -> None:
        tool = MagicMock()
        tool.name = "send_email"
        p = derive_policy_from_tool(tool)
        assert p == TOOL_POLICIES["send_email"]


# ═══════════════════════════════════════════════════════════════════════
# NATSStreamingBridge
# ═══════════════════════════════════════════════════════════════════════


class TestNATSStreamingBridge:
    async def test_publish_requires_connection(self) -> None:
        from raavan.distributed.streaming import NATSStreamingBridge

        bridge = NATSStreamingBridge.__new__(NATSStreamingBridge)
        bridge._nc = None
        bridge._js = None

        with pytest.raises(RuntimeError, match="not connected"):
            await bridge.publish("thread-1", {"type": "test"})

    async def test_subscribe_requires_connection(self) -> None:
        from raavan.distributed.streaming import NATSStreamingBridge

        bridge = NATSStreamingBridge.__new__(NATSStreamingBridge)
        bridge._nc = None
        bridge._js = None

        with pytest.raises(RuntimeError, match="not connected"):
            async for _ in bridge.subscribe("thread-1"):
                pass

    async def test_publish_serializes_json(self) -> None:
        from raavan.distributed.streaming import NATSStreamingBridge

        bridge = NATSStreamingBridge.__new__(NATSStreamingBridge)
        bridge._js = AsyncMock()
        bridge._nc = MagicMock()

        await bridge.publish("thread-1", {"type": "text_delta", "content": "hi"})

        bridge._js.publish.assert_called_once()
        call_args = bridge._js.publish.call_args
        assert call_args[0][0] == "agent.events.thread-1"
        import json

        payload = json.loads(call_args[0][1].decode("utf-8"))
        assert payload["type"] == "text_delta"
        assert payload["content"] == "hi"

    async def test_disconnect_drains(self) -> None:
        from raavan.distributed.streaming import NATSStreamingBridge

        bridge = NATSStreamingBridge.__new__(NATSStreamingBridge)
        mock_nc = AsyncMock()
        bridge._nc = mock_nc
        bridge._js = MagicMock()

        await bridge.disconnect()
        mock_nc.drain.assert_awaited_once()
        mock_nc.close.assert_awaited_once()
        assert bridge._nc is None
        assert bridge._js is None


# ═══════════════════════════════════════════════════════════════════════
# Activities
# ═══════════════════════════════════════════════════════════════════════


class TestActivities:
    def setup_method(self) -> None:
        """Set up mock DI globals for activities."""
        from raavan.distributed import activities

        self._mock_nats = AsyncMock()
        self._mock_model_client = AsyncMock()
        self._mock_memory = MagicMock()
        # Use MagicMock (not AsyncMock) so sync methods like
        # get_openai_schema() return dicts, not coroutines.
        self._mock_tool = MagicMock()
        self._mock_tool.name = "test_tool"
        self._mock_tool.get_openai_schema.return_value = {
            "type": "function",
            "function": {"name": "test_tool"},
        }

        from raavan.core.tools.base_tool import ToolResult

        # run() is async, so explicitly set it as AsyncMock
        self._mock_tool.run = AsyncMock(
            return_value=ToolResult(
                content=[{"type": "text", "text": "tool output"}],
                is_error=False,
            )
        )

        activities.configure(
            nats=self._mock_nats,
            model_client=self._mock_model_client,
            tools={"test_tool": self._mock_tool},
            redis_memory=self._mock_memory,
        )

    async def test_get_tool_schemas(self) -> None:
        from raavan.distributed import activities

        schemas = activities.get_tool_schemas()
        assert len(schemas) == 1
        assert schemas[0]["function"]["name"] == "test_tool"

    async def test_do_tool_exec_success(self) -> None:
        from raavan.distributed import activities

        result = await activities.do_tool_exec(
            tool_name="test_tool",
            arguments={"arg1": "val1"},
            thread_id="thread-1",
            timeout_seconds=30.0,
        )

        assert result["is_error"] is False
        assert "tool output" in result["content"]
        self._mock_tool.run.assert_awaited_once_with(arg1="val1")
        # Verify NATS events published
        assert self._mock_nats.publish.call_count == 2  # tool_call + tool_result

    async def test_do_tool_exec_not_found(self) -> None:
        from raavan.distributed import activities

        result = await activities.do_tool_exec(
            tool_name="nonexistent",
            arguments={},
            thread_id="thread-1",
            timeout_seconds=30.0,
        )

        assert result["is_error"] is True
        assert "not found" in result["content"]

    async def test_do_tool_exec_timeout(self) -> None:
        import asyncio

        from raavan.distributed import activities

        async def _slow(**kw: Any) -> None:
            await asyncio.sleep(10)

        self._mock_tool.run = AsyncMock(side_effect=_slow)

        result = await activities.do_tool_exec(
            tool_name="test_tool",
            arguments={},
            thread_id="thread-1",
            timeout_seconds=0.01,
        )

        assert result["is_error"] is True
        assert "timed out" in result["content"]

    @patch("raavan.integrations.memory.redis_memory.RedisMemory")
    async def test_persist_message_user(self, mock_redis_cls: MagicMock) -> None:
        from raavan.distributed import activities

        mock_mem = AsyncMock()
        mock_redis_cls.for_session.return_value = mock_mem

        result = await activities.persist_message("thread-1", "user", "Hello!")

        assert result["persisted"] is True
        mock_mem.add_message.assert_awaited_once()

    @patch("raavan.integrations.memory.redis_memory.RedisMemory")
    async def test_persist_tool_result(self, mock_redis_cls: MagicMock) -> None:
        from raavan.distributed import activities

        mock_mem = AsyncMock()
        mock_redis_cls.for_session.return_value = mock_mem

        result = await activities.persist_tool_result(
            thread_id="thread-1",
            tool_name="calculator",
            tool_call_id="tc-123",
            content="42",
            is_error=False,
        )

        assert result["persisted"] is True
        assert result["is_error"] is False
        mock_mem.add_message.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════
# RestateClient
# ═══════════════════════════════════════════════════════════════════════


class TestRestateClient:
    async def test_start_workflow(self) -> None:
        from raavan.distributed.client import RestateClient

        client = RestateClient(
            ingress_url="http://localhost:8080",
            admin_url="http://localhost:9070",
        )

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

            wf_id = await client.start_workflow(
                thread_id="thread-1",
                user_content="Hello!",
                claims={"sub": "user-1"},
            )

        assert wf_id == "thread-1"
        mock_client.post.assert_awaited_once()
        url = mock_client.post.call_args[0][0]
        assert "AgentWorkflow" in url
        assert "thread-1" in url

    async def test_resolve_promise(self) -> None:
        from raavan.distributed.client import RestateClient

        client = RestateClient()

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.resolve_promise(
                workflow_id="wf-1",
                handler_name="resolve_approval",
                value={"request_id": "req-1", "action": "approve"},
            )

        mock_client.post.assert_awaited_once()
        url = mock_client.post.call_args[0][0]
        assert "resolve_approval" in url

    async def test_cancel_workflow(self) -> None:
        from raavan.distributed.client import RestateClient

        client = RestateClient()

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.delete.return_value = mock_response
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.cancel_workflow("wf-1")

        mock_client.delete.assert_awaited_once()

    async def test_register_deployment(self) -> None:
        from raavan.distributed.client import RestateClient

        client = RestateClient()

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

            await client.register_deployment("http://worker:9080")

        mock_client.post.assert_awaited_once()
        url = mock_client.post.call_args[0][0]
        assert "deployments" in url

    async def test_url_encoding(self) -> None:
        from raavan.distributed.client import RestateClient

        client = RestateClient()

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_httpx:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_httpx.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_httpx.return_value.__aexit__ = AsyncMock(return_value=False)

            wf_id = await client.start_workflow(
                thread_id="thread with spaces",
                user_content="test",
                claims={},
                workflow_id="wf/with/slashes",
            )

            url = mock_client.post.call_args[0][0]
            # Slashes and spaces must be percent-encoded
            assert "wf%2Fwith%2Fslashes" in url
            assert wf_id == "wf/with/slashes"


# ═══════════════════════════════════════════════════════════════════════
# Workflow (structure only — Restate context is mocked)
# ═══════════════════════════════════════════════════════════════════════


class TestWorkflowDefinition:
    def test_workflow_has_main(self) -> None:
        from raavan.distributed.workflow import agent_workflow

        assert agent_workflow.name == "AgentWorkflow"
        assert "run" in agent_workflow.handlers

    def test_workflow_has_handlers(self) -> None:
        from raavan.distributed.workflow import agent_workflow

        handler_names = set(agent_workflow.handlers.keys())
        assert "resolve_approval" in handler_names
        assert "resolve_human_input" in handler_names


# ═══════════════════════════════════════════════════════════════════════
# Restate App
# ═══════════════════════════════════════════════════════════════════════


class TestRestateApp:
    def test_app_created(self) -> None:
        from raavan.distributed.restate_app import app

        assert app is not None
