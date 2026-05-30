"""Tests for BaseTool contracts: schema validation, risk levels, ToolResult."""

from __future__ import annotations

import pytest

from ravi.kernel.tools.base_tool import ToolResult, ToolRisk
from ravi.kernel.messages.content import TextBlock

from tests.fixtures.fake_tools import EchoTool, AddTool, FailTool


# ══════════════════════════════════════════════════════════════════════════════
# Registration & metadata
# ══════════════════════════════════════════════════════════════════════════════


def test_tool_has_name():
    assert EchoTool().name == "echo"


def test_tool_has_description():
    assert EchoTool().description != ""


def test_tool_risk_levels():
    assert EchoTool().risk == ToolRisk.SAFE
    assert AddTool().risk == ToolRisk.SAFE


def test_tool_color_property():
    assert EchoTool().risk.color == "green"


# ══════════════════════════════════════════════════════════════════════════════
# Execution: happy path
# ══════════════════════════════════════════════════════════════════════════════


async def test_echo_tool_returns_prefixed_message():
    result = await EchoTool().execute(message="hello")
    assert isinstance(result, ToolResult)
    assert result.content[0].text == "echo:hello"


async def test_add_tool_returns_sum():
    result = await AddTool().execute(a=10, b=32)
    assert result.content[0].text == "42"
    assert result.app_data["result"] == 42


async def test_echo_call_count_increments():
    tool = EchoTool()
    await tool.execute(message="first")
    await tool.execute(message="second")
    assert tool.call_count == 2


# ══════════════════════════════════════════════════════════════════════════════
# Execution: error path
# ══════════════════════════════════════════════════════════════════════════════


async def test_fail_tool_raises():
    with pytest.raises(RuntimeError, match="tool exploded"):
        await FailTool().execute()


# ══════════════════════════════════════════════════════════════════════════════
# ToolResult structure
# ══════════════════════════════════════════════════════════════════════════════


def test_tool_result_content_is_list():
    result = ToolResult(content=[TextBlock(text="ok")])
    assert isinstance(result.content, list)


def test_tool_result_app_data_defaults_to_none():
    result = ToolResult(content=[TextBlock(text="x")])
    assert result.app_data is None


def test_tool_result_with_app_data():
    result = ToolResult(content=[TextBlock(text="x")], app_data={"key": "value"})
    assert result.app_data["key"] == "value"


# ══════════════════════════════════════════════════════════════════════════════
# Schema: input_schema exported for LLM tool binding
# ══════════════════════════════════════════════════════════════════════════════


def test_echo_tool_schema_has_required_message():
    schema = EchoTool().input_schema
    assert "message" in schema["properties"]
    assert "message" in schema["required"]


def test_add_tool_schema_has_required_a_and_b():
    schema = AddTool().input_schema
    assert "a" in schema["required"]
    assert "b" in schema["required"]
