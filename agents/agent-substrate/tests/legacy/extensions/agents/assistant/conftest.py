"""Shared fixtures for AssistantAgent tests."""

from __future__ import annotations

import pytest

from ravi.fabric.catalog import AgentCatalogRegistry
from ravi.fabric.memory.in_memory import InMemoryHistoryProvider
from ravi.fabric.runtime.local import LocalRuntime
from ravi.reasoning.agents.assistant.agent import AssistantAgent

from tests.fixtures.mock_llm import MockLLMClient, Turn
from tests.fixtures.fake_tools import EchoTool, AddTool, FailTool, SlowTool, CounterTool


@pytest.fixture
def fresh_catalog():
    return AgentCatalogRegistry()


@pytest.fixture
def echo_tool():
    return EchoTool()


@pytest.fixture
def add_tool():
    return AddTool()


@pytest.fixture
def fail_tool():
    return FailTool()


@pytest.fixture
def slow_tool():
    return SlowTool(seconds=60.0)


@pytest.fixture
def counter_tool():
    return CounterTool()


async def make_agent(
    script: list[Turn],
    *,
    tools: list | None = None,
    max_iterations: int = 10,
    run_timeout: float | None = None,
    tool_timeout: float = 30.0,
    system_instructions: str = "You are a test agent.",
    enable_capability_search: bool = False,
    runtime: LocalRuntime | None = None,
) -> AssistantAgent:
    """Factory: build an AssistantAgent from a scripted LLM and optional tools."""
    catalog = AgentCatalogRegistry()
    catalog.register_model("primary", MockLLMClient(script=script))
    catalog.register_memory("memory", InMemoryHistoryProvider())
    for tool in tools or []:
        catalog.register_tool(tool)

    rt = runtime
    _owned_runtime = rt is None
    if rt is None:
        rt = LocalRuntime()
        await rt.start()

    agent = AssistantAgent(
        "test-agent",
        rt,
        catalog=catalog,
        system_instructions=system_instructions,
        max_iterations=max_iterations,
        run_timeout=run_timeout,
        tool_timeout=tool_timeout,
        enable_capability_search=enable_capability_search,
    )
    await agent.start()
    agent._owned_runtime = rt if _owned_runtime else None  # type: ignore[attr-defined]
    return agent
