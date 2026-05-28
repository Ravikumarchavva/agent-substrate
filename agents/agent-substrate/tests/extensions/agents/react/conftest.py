"""Shared fixtures for agent tests."""

from __future__ import annotations

import pytest

from ravi.kernel.agent_catalog import AgentCatalogRegistry
from ravi.extensions.agents.react.agent import ReActAgent
from ravi.kernel.memory.unbounded_memory import UnboundedMemory

from tests.fixtures.mock_llm import MockLLMClient, Turn
from tests.fixtures.fake_tools import EchoTool, AddTool, FailTool, SlowTool, CounterTool


@pytest.fixture
def fresh_catalog():
    """AgentCatalog with no model — caller must register one."""
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


def make_agent(
    script: list[Turn],
    *,
    tools: list | None = None,
    max_iterations: int = 10,
    run_timeout: float | None = None,
    tool_timeout: float = 30.0,
    system_instructions: str = "You are a test agent.",
    enable_capability_search: bool = False,
) -> ReActAgent:
    """Factory: build a ReActAgent from a scripted LLM and optional tools."""
    catalog = AgentCatalogRegistry()
    catalog.register_model("primary", MockLLMClient(script=script))
    catalog.register_memory("memory", UnboundedMemory())
    for tool in tools or []:
        catalog.register_tool(tool)
    return ReActAgent(
        name="test-agent",
        description="A test agent",
        catalog=catalog,
        system_instructions=system_instructions,
        max_iterations=max_iterations,
        run_timeout=run_timeout,
        tool_timeout=tool_timeout,
        enable_capability_search=enable_capability_search,
    )
