"""Unit tests for the pipeline system (schema, runner, codegen).

Uses mocks to avoid real API calls or Redis connections.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_framework.core.pipelines.schema import (
    EdgeConfig,
    EdgeType,
    NodeConfig,
    NodeType,
    PipelineConfig,
    Position,
)
from agent_framework.core.pipelines.codegen import generate_code
from agent_framework.core.pipelines.runner import PipelineRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run(coro):
    """Sync wrapper for async tests (no pytest-asyncio needed)."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_node(
    id: str = "agent_1",
    model: str = "gpt-4o-mini",
    system_prompt: str = "You are helpful.",
    max_iterations: int = 5,
) -> NodeConfig:
    return NodeConfig(
        id=id,
        node_type=NodeType.AGENT,
        label="TestAgent",
        position=Position(x=0, y=0),
        config={
            "model": model,
            "system_prompt": system_prompt,
            "max_iterations": max_iterations,
        },
    )


def _tool_node(id: str = "tool_1", tool_name: str = "calculator") -> NodeConfig:
    return NodeConfig(
        id=id,
        node_type=NodeType.TOOL,
        label="Calculator",
        position=Position(x=200, y=0),
        config={"tool_name": tool_name, "risk": "safe"},
    )


def _memory_node(id: str = "mem_1", backend: str = "unbounded") -> NodeConfig:
    return NodeConfig(
        id=id,
        node_type=NodeType.MEMORY,
        label="Memory",
        position=Position(x=200, y=100),
        config={"backend": backend, "ttl": 3600, "max_messages": 200},
    )


def _guardrail_node(
    id: str = "guard_1",
    guardrail_type: str = "output",
    schema: str = "ContentSafetyJudge",
) -> NodeConfig:
    return NodeConfig(
        id=id,
        node_type=NodeType.GUARDRAIL,
        label="SafetyJudge",
        position=Position(x=200, y=200),
        config={
            "guardrail_type": guardrail_type,
            "schema": schema,
            "pass_field": "safe",
            "system_prompt": "Evaluate safety",
        },
    )


def _skill_node(id: str = "skill_1", skill_name: str = "web-research") -> NodeConfig:
    return NodeConfig(
        id=id,
        node_type=NodeType.SKILL,
        label="WebResearch",
        position=Position(x=200, y=300),
        config={"skill_name": skill_name},
    )


def _router_node(id: str = "router_1") -> NodeConfig:
    return NodeConfig(
        id=id,
        node_type=NodeType.ROUTER,
        label="Router",
        position=Position(x=0, y=0),
        config={
            "routing_key": "intent",
            "routes": ["greeting", "question"],
            "routing_fields": [
                {"name": "intent", "type": "str", "description": "User intent"},
                {"name": "reasoning", "type": "str", "description": "Why"},
            ],
        },
    )


def _edge(source: str, target: str, edge_type: EdgeType, label: str = "") -> EdgeConfig:
    return EdgeConfig(
        id=f"{source}_{target}",
        source=source,
        target=target,
        edge_type=edge_type,
        label=label,
    )


def _mock_tool(name: str = "calculator") -> MagicMock:
    tool = MagicMock()
    tool.name = name
    tool.description = f"Test tool: {name}"
    tool.execute = AsyncMock(return_value=MagicMock(content="42", metadata={}))
    return tool


def _mock_model_client() -> MagicMock:
    client = MagicMock()
    client.model = "gpt-4o-mini"
    client.api_key = "test-key"
    client.generate_structured = AsyncMock()
    return client


# ===========================================================================
# Schema tests
# ===========================================================================


class TestPipelineConfig:
    def test_create_empty(self):
        cfg = PipelineConfig(name="Test")
        assert cfg.name == "Test"
        assert cfg.nodes == []
        assert cfg.edges == []
        assert cfg.id  # auto-generated UUID

    def test_nodes_by_type(self):
        cfg = PipelineConfig(
            name="Test",
            nodes=[
                _agent_node(),
                _tool_node(),
                _tool_node(id="tool_2", tool_name="search"),
            ],
        )
        assert len(cfg.nodes_by_type(NodeType.AGENT)) == 1
        assert len(cfg.nodes_by_type(NodeType.TOOL)) == 2
        assert len(cfg.nodes_by_type(NodeType.ROUTER)) == 0

    def test_edges_from_to(self):
        e = _edge("agent_1", "tool_1", EdgeType.AGENT_TOOL)
        cfg = PipelineConfig(name="Test", edges=[e])
        assert len(cfg.edges_from("agent_1")) == 1
        assert len(cfg.edges_to("tool_1")) == 1
        assert len(cfg.edges_from("tool_1")) == 0

    def test_node_by_id(self):
        agent = _agent_node()
        cfg = PipelineConfig(name="Test", nodes=[agent])
        assert cfg.node_by_id("agent_1") is not None
        assert cfg.node_by_id("nonexistent") is None

    def test_serialise_roundtrip(self):
        cfg = PipelineConfig(
            name="RT",
            nodes=[_agent_node(), _tool_node()],
            edges=[_edge("agent_1", "tool_1", EdgeType.AGENT_TOOL)],
        )
        json_str = cfg.model_dump_json()
        restored = PipelineConfig.model_validate_json(json_str)
        assert restored.name == "RT"
        assert len(restored.nodes) == 2
        assert len(restored.edges) == 1
        assert restored.edges[0].edge_type == EdgeType.AGENT_TOOL


class TestNodeConfig:
    def test_agent_defaults(self):
        n = _agent_node()
        assert n.node_type == NodeType.AGENT
        assert n.config["model"] == "gpt-4o-mini"

    def test_enum_values(self):
        for nt in NodeType:
            assert isinstance(nt.value, str)
        for et in EdgeType:
            assert isinstance(et.value, str)


# ===========================================================================
# Code generation tests
# ===========================================================================


class TestCodegen:
    def test_generates_valid_python(self):
        cfg = PipelineConfig(
            name="Simple Agent",
            nodes=[_agent_node()],
            edges=[],
        )
        code = generate_code(cfg)
        assert "async def main" in code
        assert "OpenAIClient" in code
        assert "gpt-4o-mini" in code
        # Should be compilable
        compile(code, "<generated>", "exec")

    def test_includes_tools(self):
        cfg = PipelineConfig(
            name="With Tools",
            nodes=[_agent_node(), _tool_node()],
            edges=[_edge("agent_1", "tool_1", EdgeType.AGENT_TOOL)],
        )
        code = generate_code(cfg)
        assert "tools=" in code
        compile(code, "<generated>", "exec")

    def test_includes_guardrails(self):
        cfg = PipelineConfig(
            name="With Guards",
            nodes=[_agent_node(), _guardrail_node()],
            edges=[_edge("agent_1", "guard_1", EdgeType.AGENT_GUARDRAIL)],
        )
        code = generate_code(cfg)
        assert "LLMJudge" in code or "guardrail" in code.lower()
        compile(code, "<generated>", "exec")

    def test_includes_router(self):
        cfg = PipelineConfig(
            name="Router Pipeline",
            nodes=[
                _router_node(),
                _agent_node(id="a1"),
                _agent_node(id="a2", model="gpt-4o"),
            ],
            edges=[
                _edge("router_1", "a1", EdgeType.ROUTER_ROUTE, label="greeting"),
                _edge("router_1", "a2", EdgeType.ROUTER_ROUTE, label="question"),
            ],
        )
        code = generate_code(cfg)
        assert "StructuredRouter" in code
        assert "greeting" in code
        compile(code, "<generated>", "exec")


# ===========================================================================
# Runner tests
# ===========================================================================


class TestPipelineRunner:
    def test_build_simple_agent(self):
        """Build a minimal pipeline with one agent → verifies ReActAgent returned."""

        async def _inner():
            cfg = PipelineConfig(
                name="Simple",
                nodes=[_agent_node()],
                edges=[],
            )
            runner = PipelineRunner()
            agent = await runner.build(
                cfg,
                tools_registry=[],
                model_client=_mock_model_client(),
            )
            from agent_framework.core.agents.react_agent import ReActAgent

            assert isinstance(agent, ReActAgent)
            assert agent.name == "TestAgent"

        _run(_inner())

    def test_build_agent_with_tools(self):
        """Agent connected to a tool → tool appears in agent.tools."""

        async def _inner():
            mock_tool = _mock_tool("calculator")
            cfg = PipelineConfig(
                name="With Tools",
                nodes=[_agent_node(), _tool_node()],
                edges=[_edge("agent_1", "tool_1", EdgeType.AGENT_TOOL)],
            )
            runner = PipelineRunner()
            agent = await runner.build(
                cfg,
                tools_registry=[mock_tool],
                model_client=_mock_model_client(),
            )
            assert len(agent.tools) == 1
            assert agent.tools[0].name == "calculator"

        _run(_inner())

    def test_build_agent_with_unbounded_memory(self):
        """Agent + unbounded memory node → uses UnboundedMemory."""

        async def _inner():
            cfg = PipelineConfig(
                name="With Memory",
                nodes=[_agent_node(), _memory_node()],
                edges=[_edge("agent_1", "mem_1", EdgeType.AGENT_MEMORY)],
            )
            runner = PipelineRunner()
            agent = await runner.build(
                cfg,
                tools_registry=[],
                model_client=_mock_model_client(),
            )
            from agent_framework.core.memory.unbounded_memory import UnboundedMemory

            assert isinstance(agent.memory, UnboundedMemory)

        _run(_inner())

    def test_build_missing_agent_raises(self):
        """Pipeline with no agent or router → ValueError."""

        async def _inner():
            cfg = PipelineConfig(name="Empty", nodes=[_tool_node()], edges=[])
            runner = PipelineRunner()
            with pytest.raises(ValueError, match="no agent"):
                await runner.build(
                    cfg,
                    tools_registry=[],
                    model_client=_mock_model_client(),
                )

        _run(_inner())

    def test_tool_not_in_registry_skipped(self):
        """Tool node referencing an unregistered tool → silently skipped."""

        async def _inner():
            cfg = PipelineConfig(
                name="Missing Tool",
                nodes=[_agent_node(), _tool_node(tool_name="nonexistent")],
                edges=[_edge("agent_1", "tool_1", EdgeType.AGENT_TOOL)],
            )
            runner = PipelineRunner()
            agent = await runner.build(
                cfg,
                tools_registry=[_mock_tool("calculator")],  # not "nonexistent"
                model_client=_mock_model_client(),
            )
            assert len(agent.tools) == 0  # tool was skipped

        _run(_inner())

    def test_build_router(self):
        """Router node with 2 route edges → StructuredRouter returned."""

        async def _inner():
            cfg = PipelineConfig(
                name="Router",
                nodes=[
                    _router_node(),
                    _agent_node(id="a1"),
                    _agent_node(id="a2"),
                ],
                edges=[
                    _edge("router_1", "a1", EdgeType.ROUTER_ROUTE, label="greeting"),
                    _edge("router_1", "a2", EdgeType.ROUTER_ROUTE, label="question"),
                ],
            )
            runner = PipelineRunner()
            result = await runner.build(
                cfg,
                tools_registry=[],
                model_client=_mock_model_client(),
            )
            from agent_framework.core.structured.router import StructuredRouter

            assert isinstance(result, StructuredRouter)

        _run(_inner())

    def test_build_routing_schema(self):
        """Dynamic Pydantic model from routing_fields config."""
        runner = PipelineRunner()
        cfg = {
            "routing_fields": [
                {"name": "intent", "type": "str", "description": "User intent"},
                {"name": "confidence", "type": "float", "description": "Score"},
            ]
        }
        schema = runner._build_routing_schema(cfg)
        assert "intent" in schema.model_fields
        assert "confidence" in schema.model_fields

    def test_build_routing_schema_fallback(self):
        """Empty routing_fields → default category + reasoning."""
        runner = PipelineRunner()
        schema = runner._build_routing_schema({})
        assert "category" in schema.model_fields
        assert "reasoning" in schema.model_fields
