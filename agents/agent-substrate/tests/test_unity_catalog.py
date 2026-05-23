from __future__ import annotations

import pytest
from typing import List

from ravi.core.tools.base_tool import BaseTool, ToolResult, ToolRisk
from ravi.core.catalog import AgentCatalogRegistry, CatalogAsset
from ravi.core.middleware.base import MiddlewareContext, MiddlewareStage
from ravi.core.middleware.builtins.governance import GovernanceMiddleware
from ravi.exceptions import GuardrailTripwireError
from ravi.core.memory.unbounded_memory import UnboundedMemory
from ravi.core.context.implementations import SlidingWindowContext
from ravi.core.checkpointing import CheckpointStore


class DummyTaxTool(BaseTool):
    def __init__(self) -> None:
        super().__init__(name="calculate_tax", description="Calculates tax")

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(content=[{"type": "text", "text": "tax calc ok"}])


@pytest.mark.asyncio
async def test_three_level_namespace_registration():
    cat = AgentCatalogRegistry(default_catalog="main")
    tool = DummyTaxTool()

    # Register in custom schema
    cat.register_tool(tool, catalog="main", schema="finance")

    # Verify FQN lookup
    asset = cat.get_asset("main.finance.calculate_tax")
    assert asset is not None
    assert asset.fqn == "main.finance.calculate_tax"
    assert asset.asset_type == "tool"
    assert asset.tool == tool


@pytest.mark.asyncio
async def test_sql_style_short_name_resolution():
    cat = AgentCatalogRegistry(default_catalog="main")
    tool = DummyTaxTool()
    cat.register_tool(tool, catalog="main", schema="finance")

    # If search path does not contain finance, it shouldn't resolve
    fqn_none = cat.resolve_fqn("calculate_tax", ["default", "system"])
    assert fqn_none is None

    # Resolve with search path containing finance
    fqn = cat.resolve_fqn("calculate_tax", ["default", "finance"])
    assert fqn == "main.finance.calculate_tax"

    # get_tool should dynamically resolve
    resolved_tool = cat.get_tool("calculate_tax", search_path=["finance"])
    assert resolved_tool == tool


@pytest.mark.asyncio
async def test_stateful_catalog_assets():
    cat = AgentCatalogRegistry(default_catalog="main")
    memory = UnboundedMemory()
    context = SlidingWindowContext(max_messages=15)

    # Register memory and context strategies in education schema
    cat.register_memory("chat_memory", memory, catalog="main", schema="education")
    cat.register_context("model_context", context, catalog="main", schema="education")

    # Verify lookups
    retrieved_memory = cat.get_memory("chat_memory", search_path=["education"])
    assert retrieved_memory == memory

    retrieved_context = cat.get_context("model_context", search_path=["education"])
    assert retrieved_context == context


@pytest.mark.asyncio
async def test_unity_catalog_wildcard_grants():
    cat = AgentCatalogRegistry(default_catalog="main")
    tool = DummyTaxTool()
    cat.register_tool(tool, catalog="main", schema="finance")
    fqn = "main.finance.calculate_tax"

    # Default to open access when no grants exist at all
    assert cat.check_permission("analyst", fqn, "execute") is True

    # Configure a grant to principal 'manager' for all finance assets
    cat.grant_privilege("execute", "main.finance.*", "manager")

    # Configure a grant to principal 'analyst' for the exact tax asset
    cat.grant_privilege("execute", fqn, "analyst")

    # Configure a grant to principal 'admin' for the entire catalog
    cat.grant_privilege("execute", "main.*", "admin")

    # Analyst can execute calculate_tax
    assert cat.check_permission("analyst", fqn, "execute") is True
    # Manager can execute calculate_tax via wildcard finance.*
    assert cat.check_permission("manager", fqn, "execute") is True
    # Admin can execute calculate_tax via wildcard main.*
    assert cat.check_permission("admin", fqn, "execute") is True

    # A random stranger has no privileges now since explicit rules are defined
    assert cat.check_permission("stranger", fqn, "execute") is False


@pytest.mark.asyncio
async def test_governance_middleware_enforcement():
    cat = AgentCatalogRegistry(default_catalog="main")
    tool = DummyTaxTool()
    cat.register_tool(tool, catalog="main", schema="finance")

    # Set up explicit grant (which implicitly restricts others)
    cat.grant_privilege("execute", "main.finance.calculate_tax", "finance_agent")

    middleware = GovernanceMiddleware(catalog=cat)

    # Valid execution: agent has access
    ctx_allowed = MiddlewareContext(
        stage=MiddlewareStage.TOOL_EXECUTION,
        agent_name="finance_agent",
        tool_name="calculate_tax",
        metadata={"search_path": ["finance"]}
    )
    result_ctx = await middleware.before(ctx_allowed)
    assert result_ctx == ctx_allowed

    # Invalid execution: unauthorized agent
    ctx_blocked = MiddlewareContext(
        stage=MiddlewareStage.TOOL_EXECUTION,
        agent_name="unauthorized_agent",
        tool_name="calculate_tax",
        metadata={"search_path": ["finance"]}
    )

    with pytest.raises(GuardrailTripwireError) as exc_info:
        await middleware.before(ctx_blocked)

    assert "unauthorized_agent" in str(exc_info.value)
    assert "main.finance.calculate_tax" in str(exc_info.value)
    assert exc_info.value.guardrail_name == "governance_policy"
