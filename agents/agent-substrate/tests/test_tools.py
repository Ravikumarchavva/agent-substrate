"""Tests for the tool system — BaseTool, ToolResult, ToolRegistry."""

from __future__ import annotations

import pytest
from raavan.core.tools.base_tool import BaseTool, ToolResult


class EchoTool(BaseTool):
    """Simple test tool that echoes input."""

    def __init__(self) -> None:
        super().__init__(
            name="echo",
            description="Echoes the input back",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to echo"},
                },
                "required": ["message"],
            },
        )

    async def execute(self, **kwargs: str) -> ToolResult:
        msg = kwargs.get("message", "")
        return ToolResult(
            content=[{"type": "text", "text": f"Echo: {msg}"}],
            app_data={"original": msg},
        )


class TestBaseTool:
    def test_tool_name(self) -> None:
        tool = EchoTool()
        assert tool.name == "echo"

    def test_tool_description(self) -> None:
        tool = EchoTool()
        assert tool.description == "Echoes the input back"

    def test_tool_schema(self) -> None:
        tool = EchoTool()
        schema = tool.get_schema()
        assert schema.name == "echo"
        assert "message" in str(schema.inputSchema)

    def test_tool_openai_schema(self) -> None:
        tool = EchoTool()
        schema = tool.get_openai_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "echo"

    @pytest.mark.asyncio
    async def test_tool_execute(self) -> None:
        tool = EchoTool()
        result = await tool.execute(message="hello world")
        assert result.content == [{"type": "text", "text": "Echo: hello world"}]
        assert result.app_data == {"original": "hello world"}


class TestToolResult:
    def test_tool_result_creation(self) -> None:
        result = ToolResult(content=[{"type": "text", "text": "test output"}])
        assert result.content == [{"type": "text", "text": "test output"}]

    def test_tool_result_with_metadata(self) -> None:
        result = ToolResult(
            content=[{"type": "text", "text": "output"}],
            app_data={"key": "value"},
        )
        assert result.app_data == {"key": "value"}
