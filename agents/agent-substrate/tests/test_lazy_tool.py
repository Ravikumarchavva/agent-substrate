from __future__ import annotations

import pytest
from typing import Dict, Any

from ravi.core.tools.base_tool import BaseTool, ToolResult, ToolRisk
from ravi.core.catalog import AgentCatalogRegistry
from ravi.core.catalog import LazyTool


class HeavyDummyTool(BaseTool):
    """A mock heavy tool that counts its instantiations to verify lazy evaluation."""

    instances_created = 0

    def __init__(self, name: str = "heavy_dummy") -> None:
        super().__init__(
            name=name,
            description="A dummy heavy tool",
            input_schema={
                "type": "object",
                "properties": {
                    "val": {"type": "string"}
                },
                "required": ["val"]
            }
        )
        type(self).instances_created += 1

    async def execute(self, val: str) -> ToolResult:
        return ToolResult(content=[{"type": "text", "text": f"processed: {val}"}])


@pytest.mark.asyncio
async def test_lazy_tool_deferred_instantiation():
    # Reset instance counter
    HeavyDummyTool.instances_created = 0

    # Define factory function
    def factory() -> BaseTool:
        return HeavyDummyTool()

    # The tool should not be instantiated yet
    assert HeavyDummyTool.instances_created == 0

    # Create the lazy tool wrapper
    lazy_tool = LazyTool(
        name="heavy_dummy",
        description="A dummy heavy tool",
        factory_fn=factory,
        input_schema={
            "type": "object",
            "properties": {
                "val": {"type": "string"}
            },
            "required": ["val"]
        },
        risk=ToolRisk.SENSITIVE,
        category="data",
        tags=["heavy", "dummy"],
        aliases=["dummy_lazy"]
    )

    # Still no instantiation
    assert HeavyDummyTool.instances_created == 0

    # Verify metadata is accessible on the LazyTool wrapper
    assert lazy_tool.name == "heavy_dummy"
    assert lazy_tool.description == "A dummy heavy tool"
    assert lazy_tool.risk == ToolRisk.SENSITIVE
    assert lazy_tool.category == "data"
    assert "heavy" in lazy_tool.tags

    # Resolve/Execute should trigger instantiation
    res = await lazy_tool.run(val="hello")
    assert HeavyDummyTool.instances_created == 1
    assert "processed: hello" in str(res.content)

    # Subsequent run should reuse the same cached instance
    res2 = await lazy_tool.run(val="world")
    assert HeavyDummyTool.instances_created == 1
    assert "processed: world" in str(res2.content)


@pytest.mark.asyncio
async def test_catalog_register_lazy_tool():
    HeavyDummyTool.instances_created = 0
    cat = AgentCatalogRegistry()

    def factory():
        return HeavyDummyTool()

    cat.register_lazy_tool(
        name="lazy_calc",
        factory_fn=factory,
        description="A lazy dummy calc",
        category="productivity",
        tags=["lazy", "calc"]
    )

    # Verify registry has the tool, but it's not yet instantiated
    assert "lazy_calc" in cat
    assert HeavyDummyTool.instances_created == 0

    entry = cat.get("lazy_calc")
    assert entry is not None
    assert entry.kind == "tool"
    assert entry.category == "productivity"

    # Fetch tool from catalog
    tool = cat.get_tool("lazy_calc")
    assert isinstance(tool, LazyTool)
    assert HeavyDummyTool.instances_created == 0

    # Run it through catalog lookup
    res = await tool.run(val="test")
    assert HeavyDummyTool.instances_created == 1
    assert "processed: test" in str(res.content)
