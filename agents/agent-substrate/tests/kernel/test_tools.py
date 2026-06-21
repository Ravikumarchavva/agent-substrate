from __future__ import annotations

from substrate.kernel.tools import (
    ToolCallRequest,
    ToolExecutionResult,
    ToolRisk,
)
from substrate.agents.tools.toolbox import Toolbox
from substrate.kernel.core.content import TextBlock


class MockToolImpl:
    name = "mock_tool"
    description = "A mock tool for testing."
    risk = ToolRisk.HIGH
    input_schema = {
        "type": "object",
        "properties": {"val": {"type": "string"}},
        "required": ["val"],
    }

    async def execute(self, *, val: str, **_kw: object) -> ToolExecutionResult:
        return ToolExecutionResult(
            name=self.name, content=[TextBlock(text=f"executed with {val}")]
        )


def test_tool_risk_enum():
    assert ToolRisk.SAFE == "safe"
    assert ToolRisk.HIGH == "high"
    assert ToolRisk.CRITICAL == "critical"


def test_tool_call_request():
    req = ToolCallRequest(name="test_tool", arguments={"x": 1}, call_id="c123")
    assert req.name == "test_tool"
    assert req.arguments == {"x": 1}
    assert req.call_id == "c123"


def test_tool_execution_result():
    res = ToolExecutionResult(
        call_id="c123",
        name="test_tool",
        content=[TextBlock(text="done")],
        is_error=False,
    )
    assert res.call_id == "c123"
    assert res.name == "test_tool"
    assert res.is_error is False
    assert res.text == "done"


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

    # Test schema_for
    schema = registry.schema_for("mock_tool")
    assert schema is not None
    assert schema["name"] == "mock_tool"
    assert schema["description"] == "A mock tool for testing."
    assert schema["parameters"] == tool.input_schema

    # Test deferred schemas
    defs = registry.deferred_schemas(include_tool_search=True)
    assert len(defs) == 2
    assert defs[0]["name"] == "mock_tool"
    assert defs[0]["defer_loading"] is True
    assert defs[1] == {"type": "tool_search"}
