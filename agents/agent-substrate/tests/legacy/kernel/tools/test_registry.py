"""Tests for AgentCatalog tool registration and lookup."""

from __future__ import annotations

import pytest

from ravi.fabric.catalog import AgentCatalogRegistry

from tests.fixtures.fake_tools import EchoTool, AddTool


@pytest.fixture
def catalog():
    return AgentCatalogRegistry()


# ══════════════════════════════════════════════════════════════════════════════
# Register & retrieve
# ══════════════════════════════════════════════════════════════════════════════


def test_register_and_get_tool(catalog):
    catalog.register_tool(EchoTool())
    tool = catalog.get_tool("echo")
    assert tool is not None
    assert tool.name == "echo"


def test_get_unknown_tool_returns_none(catalog):
    assert catalog.get_tool("nonexistent") is None


def test_register_multiple_tools(catalog):
    catalog.register_tool(EchoTool())
    catalog.register_tool(AddTool())
    assert catalog.get_tool("echo") is not None
    assert catalog.get_tool("add") is not None


def test_all_tools_returns_registered(catalog):
    catalog.register_tool(EchoTool())
    catalog.register_tool(AddTool())
    names = {t.name for t in catalog.all_tools()}
    assert "echo" in names
    assert "add" in names


# ══════════════════════════════════════════════════════════════════════════════
# Duplicate registration
# ══════════════════════════════════════════════════════════════════════════════


def test_registering_same_name_raises(catalog):
    """Registering a tool under an existing FQN is a hard error."""
    catalog.register_tool(EchoTool())
    with pytest.raises(ValueError, match="already registered"):
        catalog.register_tool(EchoTool())


def test_registering_same_name_in_different_schema_succeeds(catalog):
    """Different schema → different FQN → both are accepted."""
    catalog.register_tool(EchoTool(), schema="default")
    catalog.register_tool(EchoTool(), schema="finance")
    assert catalog.get_tool("echo") is not None


# ══════════════════════════════════════════════════════════════════════════════
# Model registration
# ══════════════════════════════════════════════════════════════════════════════


def test_register_and_get_model(catalog):
    from tests.fixtures.mock_llm import MockLLMClient
    llm = MockLLMClient()
    catalog.register_model("primary", llm)
    model = catalog.primary_model()
    assert model is llm


def test_no_model_registered_returns_none(catalog):
    assert catalog.primary_model() is None


# ══════════════════════════════════════════════════════════════════════════════
# Memory registration
# ══════════════════════════════════════════════════════════════════════════════


def test_register_and_get_memory(catalog):
    from ravi.fabric.memory.in_memory import InMemoryHistoryProvider
    mem = InMemoryHistoryProvider()
    catalog.register_memory("main", mem)
    assert catalog.primary_memory() is mem


def test_no_memory_returns_none(catalog):
    assert catalog.primary_memory() is None
