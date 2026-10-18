from __future__ import annotations

from loamz.agents.kernel.tools import Toolbox, ToolExecutionResult, ToolRisk
from loamz.agents.kernel.types.content import TextBlock


class MockToolImpl:
    idempotent = True
    name = "mock_tool"
    description = "A mock tool for testing."
    risk = ToolRisk.HIGH
    input_schema = {
        "type": "object",
        "properties": {"val": {"type": "string"}},
        "required": ["val"],
    }

    async def execute(self, *, val: str, **_kw: object) -> ToolExecutionResult:
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=f"executed with {val}")])


def test_tool_registry():
    registry = Toolbox()
    tool = MockToolImpl()

    registry.add(tool)
    assert len(registry) == 1
    assert "mock_tool" in registry
    assert registry.get("mock_tool") is tool
    assert registry.get("mock_tool") is tool
    assert registry.names() == ["mock_tool"]

    # Test by_risk
    assert registry.by_risk(ToolRisk.HIGH) == [tool]
    assert registry.by_risk(ToolRisk.SAFE) == []
