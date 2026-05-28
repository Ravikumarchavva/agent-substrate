"""Shared fixtures for all core tests."""

from __future__ import annotations

import pytest

from ravi.kernel.agent_catalog import AgentCatalogRegistry
from ravi.kernel.memory.unbounded_memory import UnboundedMemory
from tests.fixtures.mock_llm import MockLLMClient


@pytest.fixture
def empty_catalog():
    return AgentCatalogRegistry()


@pytest.fixture
def catalog_with_llm():
    cat = AgentCatalogRegistry()
    cat.register_model("primary", MockLLMClient())
    cat.register_memory("memory", UnboundedMemory())
    return cat
