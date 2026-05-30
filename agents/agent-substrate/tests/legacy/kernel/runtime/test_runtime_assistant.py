"""Tests for RuntimeAssistantAgent — LLM-powered Layer 2 actor agent."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from typing import AsyncIterator
import pytest

from ravi.reasoning.agents.assistant.agent import AssistantAgent as RuntimeAssistantAgent
from ravi.fabric.catalog import AgentCatalogRegistry
from ravi.fabric.runtime.local import LocalRuntime
from ravi.kernel.messages.client_messages import AssistantMessage
from ravi.fabric.agents_base.agent_result import RunStatus
from ravi.kernel.guardrails.base_guardrail import BaseGuardrail
from ravi.kernel.tools.base_tool import BaseTool, ToolResult


class TestRuntimeAssistantAgent:
    @pytest.fixture
    def mock_model_client(self) -> MagicMock:
        client = MagicMock()
        client.generate = AsyncMock()
        return client

    @pytest.fixture
    def mock_model_context(self) -> MagicMock:
        context = MagicMock()
        context.build = AsyncMock(return_value=[])
        return context

    @pytest.fixture
    async def runtime(self) -> AsyncIterator[LocalRuntime]:
        rt = LocalRuntime()
        await rt.start()
        yield rt
        await rt.stop()

    @pytest.mark.asyncio
    async def test_simple_chat_turn(
        self,
        runtime: LocalRuntime,
        mock_model_client: MagicMock,
        mock_model_context: MagicMock,
    ) -> None:
        # LLM returns a simple text answer immediately
        mock_model_client.generate.return_value = AssistantMessage(
            content=["Hello! I am a runtime agent."],
            tool_calls=None,
        )

        catalog = AgentCatalogRegistry()
        catalog.register_model("primary", mock_model_client)
        catalog.register_context("default", mock_model_context)
        agent = RuntimeAssistantAgent(
            name="helper",
            runtime=runtime,
            catalog=catalog,
            enable_capability_search=False,
        )
        await agent.start()

        # Send a message to the agent
        response = await runtime.send_message("Hi", recipient=agent.id)
        assert response.status == RunStatus.COMPLETED
        assert response.output == ["Hello! I am a runtime agent."]
        mock_model_client.generate.assert_called_once()

    @pytest.mark.asyncio
    async def test_tool_calling_loop(
        self,
        runtime: LocalRuntime,
        mock_model_client: MagicMock,
        mock_model_context: MagicMock,
    ) -> None:
        from ravi.kernel.messages.client_messages import ToolCallMessage

        # Step 1: LLM returns a tool call
        # Step 2: LLM returns a final text answer
        mock_model_client.generate.side_effect = [
            AssistantMessage(
                content=None,
                tool_calls=[
                    ToolCallMessage(
                        id="call-123",
                        name="get_weather",
                        arguments={"location": "San Francisco"},
                    )
                ],
            ),
            AssistantMessage(
                content=["It is sunny in San Francisco."],
                tool_calls=None,
            ),
        ]

        # Define a mock tool
        weather_tool = MagicMock(spec=BaseTool)
        weather_tool.name = "get_weather"
        weather_tool.description = "Get current weather"
        weather_tool.parameters = {}
        weather_tool.input_schema = {}
        weather_tool.execute = AsyncMock(
            return_value=ToolResult(output_text="Sunny, 68F")
        )

        catalog = AgentCatalogRegistry()
        catalog.register_model("primary", mock_model_client)
        catalog.register_context("default", mock_model_context)
        catalog.register_tool(weather_tool)
        agent = RuntimeAssistantAgent(
            name="weather_agent",
            runtime=runtime,
            catalog=catalog,
            enable_capability_search=False,
        )
        await agent.start()

        response = await runtime.send_message(
            "What is the weather like in SF?", recipient=agent.id
        )
        assert response.status == RunStatus.COMPLETED
        assert response.output == ["It is sunny in San Francisco."]

        # Verify tool was called and model generated twice
        weather_tool.execute.assert_called_once_with(location="San Francisco")
        assert mock_model_client.generate.call_count == 2

    @pytest.mark.asyncio
    async def test_input_guardrail_trip(
        self,
        runtime: LocalRuntime,
        mock_model_client: MagicMock,
        mock_model_context: MagicMock,
    ) -> None:
        # Define a guardrail that raises an exception
        from ravi.reasoning.middleware.guardrails import GuardrailsMiddleware
        from ravi.kernel.guardrails.base_guardrail import GuardrailType, GuardrailResult

        guardrail = MagicMock(spec=BaseGuardrail)
        guardrail.name = "test-input-guardrail"
        guardrail.guardrail_type = GuardrailType.INPUT
        guardrail.check = AsyncMock(
            return_value=GuardrailResult(
                guardrail_name="test-input-guardrail",
                guardrail_type=GuardrailType.INPUT,
                passed=False,
                tripwire=True,
                message="Spam detected",
            )
        )

        catalog = AgentCatalogRegistry()
        catalog.register_model("primary", mock_model_client)
        catalog.register_context("default", mock_model_context)
        agent = RuntimeAssistantAgent(
            name="safe_agent",
            runtime=runtime,
            catalog=catalog,
            enable_capability_search=False,
            middleware=[GuardrailsMiddleware(input_guardrails=[guardrail])],
        )
        await agent.start()

        response = await runtime.send_message("spam", recipient=agent.id)
        assert response.status == RunStatus.GUARDRAIL_TRIPPED
        assert "Request blocked" in str(response.output)
        mock_model_client.generate.assert_not_called()
