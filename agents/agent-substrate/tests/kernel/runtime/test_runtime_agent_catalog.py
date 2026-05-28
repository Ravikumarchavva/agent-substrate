from __future__ import annotations

import pytest
from typing import Any, Optional

from ravi.kernel.tools.base_tool import BaseTool, ToolResult
from ravi.kernel.agent_catalog import AgentCatalogRegistry
from ravi.extensions.agents.runtime.agent import RuntimeAgent
from ravi.extensions.agents.runtime.assistant_agent import RuntimeAssistantAgent
from ravi.kernel.runtime._protocol import AgentRuntime
from ravi.kernel.runtime._identity import TopicId, AgentId
from ravi.kernel.runtime._contracts import MessageContext
from ravi.kernel.llm.base_client import BaseModelClient
from ravi.kernel.messages.client_messages import AssistantMessage
from ravi.extensions.context.redis_model_context import SlidingWindowContext


class DummyCatalogTool(BaseTool):
    def __init__(self, name: str = "dummy_catalog_tool") -> None:
        super().__init__(name=name, description="A dummy catalog tool")

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(content=[{"type": "text", "text": "catalog tool ok"}])


class MockAgentRuntime(AgentRuntime):
    async def start(self) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def register(self, type_name: str, handler: Any) -> None:
        pass

    async def subscribe(self, type_name: str, topic: TopicId) -> None:
        pass

    async def send_message(
        self, message: Any, sender: Optional[AgentId], recipient: AgentId
    ) -> Any:
        pass

    async def publish_message(
        self, message: Any, sender: Optional[AgentId], topic: TopicId
    ) -> None:
        pass


class MockModelClient(BaseModelClient):
    def __init__(self) -> None:
        super().__init__(model="mock-model")

    async def generate(
        self, messages: Any, tools: Any = None, **kwargs: Any
    ) -> AssistantMessage:
        return AssistantMessage(role="assistant", content=["mock response"])

    async def generate_stream(self, messages: Any, tools: Any = None, **kwargs: Any):
        yield AssistantMessage(role="assistant", content=["mock response"])

    async def count_tokens(self, messages: Any) -> int:
        return 0


@pytest.mark.asyncio
async def test_runtime_agent_catalog_unification():
    runtime = MockAgentRuntime()
    cat = AgentCatalogRegistry()
    tool = DummyCatalogTool()
    cat.register_tool(tool)

    # Instantiate RuntimeAgent with explicit catalog
    agent = RuntimeAgent(name="test_runtime_agent", runtime=runtime, catalog=cat)

    # Dynamic tools property should fetch from the catalog
    assert len(agent.tools) == 1
    assert agent.tools[0].name == "dummy_catalog_tool"

    # Adding tools through tools setter should update the catalog
    new_tool = DummyCatalogTool(name="another_tool")
    agent.tools = [new_tool]

    assert len(agent.tools) == 1
    assert agent.tools[0].name == "another_tool"
    assert "another_tool" in agent.catalog
    assert "dummy_catalog_tool" not in agent.catalog


@pytest.mark.asyncio
async def test_runtime_assistant_agent_catalog_schemas():
    runtime = MockAgentRuntime()
    client = MockModelClient()
    context = SlidingWindowContext(max_messages=10)
    cat = AgentCatalogRegistry()
    cat.register_tool(DummyCatalogTool())

    # Instantiate RuntimeAssistantAgent with explicit catalog
    assistant = RuntimeAssistantAgent(
        name="assistant",
        runtime=runtime,
        model_client=client,
        model_context=context,
        catalog=cat,
        verbose=False,
    )

    assert len(assistant.tools) == 1
    assert assistant.tools[0].name == "dummy_catalog_tool"

    # Triggers on_message to verify it compiles schemas correctly from catalog
    ctx = MessageContext(
        runtime=runtime,
        sender=None,
        agent_id=AgentId("user", "1"),
        correlation_id="test-corr",
    )
    from ravi.kernel.messages.content import TextBlock

    res = await assistant.on_message(ctx, [TextBlock(text="hello")])
    assert res == "mock response"
