"""Tests for CapabilitySearchTool — the meta-tool for discovering capabilities."""

from __future__ import annotations

from raavan.core.tools.base_tool import BaseTool, ToolResult
from raavan.core.tools.catalog import CapabilityRegistry
from raavan.catalog.tools.capability_search.tool import CapabilitySearchTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _Stub(BaseTool):
    def __init__(self, name: str = "stub", description: str = "A stub", **kw) -> None:
        super().__init__(name=name, description=description, **kw)

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(content=[{"type": "text", "text": "ok"}])


def _make_populated_catalog() -> CapabilityRegistry:
    """Create a catalog with a few items for testing."""
    cat = CapabilityRegistry()
    cat.register_tool(
        _Stub(name="calculator", description="Perform arithmetic"),
        category="productivity",
        tags=["math", "calculate", "arithmetic"],
    )
    cat.register_tool(
        _Stub(name="web_surfer", description="Browse the internet"),
        category="research",
        tags=["web", "browse", "internet"],
        aliases=["browser"],
    )
    cat.register_tool(
        _Stub(
            name="data_visualizer",
            description="Create charts and graphs",
            input_schema={
                "type": "object",
                "properties": {
                    "data": {"type": "array"},
                    "chart_type": {"type": "string"},
                },
            },
        ),
        category="data/visualization",
        tags=["chart", "graph", "plot"],
    )

    class FakeSkill:
        name = "debugging"
        description = "Step-by-step debugging methodology"
        category = "development/execution"
        tags = ["debug", "error", "trace"]
        aliases = ["debugger"]

    cat.register_skill(FakeSkill())
    return cat


# ---------------------------------------------------------------------------
# Search action
# ---------------------------------------------------------------------------


async def test_search_returns_results():
    cat = _make_populated_catalog()
    tool = CapabilitySearchTool(cat)

    result = await tool.execute(action="search", query="math calculate")

    assert not result.is_error
    text = result.content[0]["text"]
    assert "calculator" in text
    assert result.app_data["matched_tool_names"] == ["calculator"]


async def test_search_finds_skills():
    cat = _make_populated_catalog()
    tool = CapabilitySearchTool(cat)

    result = await tool.execute(action="search", query="debug error")

    assert not result.is_error
    assert "debugging" in result.app_data["matched_skill_names"]


async def test_search_kind_filter():
    cat = _make_populated_catalog()
    tool = CapabilitySearchTool(cat)

    result = await tool.execute(action="search", query="web", kind="tool")

    assert not result.is_error
    assert "web_surfer" in result.app_data["matched_tool_names"]
    assert result.app_data["matched_skill_names"] == []


async def test_search_empty_query_is_error():
    cat = _make_populated_catalog()
    tool = CapabilitySearchTool(cat)

    result = await tool.execute(action="search", query="")

    assert result.is_error
    assert "query" in result.content[0]["text"].lower()


async def test_search_no_results():
    cat = _make_populated_catalog()
    tool = CapabilitySearchTool(cat)

    result = await tool.execute(action="search", query="zzzznonexistentzzzz")

    assert not result.is_error
    text = result.content[0]["text"]
    assert "No matching" in text
    assert result.app_data["matched_tool_names"] == []
    assert result.app_data["matched_skill_names"] == []


async def test_search_excludes_self():
    """The search tool should not return itself."""
    cat = _make_populated_catalog()
    search_tool = CapabilitySearchTool(cat)
    cat.register_tool(search_tool)

    result = await search_tool.execute(action="search", query="search tool")

    tool_names = result.app_data.get("matched_tool_names", [])
    assert "capability_search" not in tool_names


async def test_search_shows_parameters():
    """Search results should include parameter names for tools."""
    cat = _make_populated_catalog()
    tool = CapabilitySearchTool(cat)

    result = await tool.execute(action="search", query="chart graph visualization")

    text = result.content[0]["text"]
    assert "data_visualizer" in text
    assert "parameters:" in text


# ---------------------------------------------------------------------------
# Browse action
# ---------------------------------------------------------------------------


async def test_browse_category():
    cat = _make_populated_catalog()
    tool = CapabilitySearchTool(cat)

    result = await tool.execute(action="browse", category_path="productivity")

    assert not result.is_error
    text = result.content[0]["text"]
    assert "calculator" in text
    assert result.app_data["matched_tool_names"] == ["calculator"]


async def test_browse_subcategory():
    cat = _make_populated_catalog()
    tool = CapabilitySearchTool(cat)

    result = await tool.execute(action="browse", category_path="data/visualization")

    assert not result.is_error
    text = result.content[0]["text"]
    assert "data_visualizer" in text


async def test_browse_empty_path_is_error():
    cat = _make_populated_catalog()
    tool = CapabilitySearchTool(cat)

    result = await tool.execute(action="browse", category_path="")

    assert result.is_error


async def test_browse_nonexistent_category_is_error():
    cat = _make_populated_catalog()
    tool = CapabilitySearchTool(cat)

    result = await tool.execute(action="browse", category_path="nonexistent/thing")

    assert result.is_error
    text = result.content[0]["text"]
    assert "not found" in text.lower()


# ---------------------------------------------------------------------------
# List categories action
# ---------------------------------------------------------------------------


async def test_list_categories():
    cat = _make_populated_catalog()
    tool = CapabilitySearchTool(cat)

    result = await tool.execute(action="list_categories")

    assert not result.is_error
    text = result.content[0]["text"]
    # Should include top-level categories
    assert "system" in text
    assert "productivity" in text
    assert "research" in text
    assert "data" in text


# ---------------------------------------------------------------------------
# Unknown / invalid action
# ---------------------------------------------------------------------------


async def test_unknown_action_is_error():
    cat = _make_populated_catalog()
    tool = CapabilitySearchTool(cat)

    result = await tool.execute(action="explode")

    assert result.is_error
    assert "Unknown action" in result.content[0]["text"]


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------


def test_constructor_rejects_non_catalog():
    import pytest

    with pytest.raises(TypeError, match="CapabilityRegistry"):
        CapabilitySearchTool(catalog="not a catalog")
