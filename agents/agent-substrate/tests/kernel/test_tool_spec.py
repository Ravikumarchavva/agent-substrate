"""Tests for the kernel tool taxonomy: AnyTool TypeGuards."""

from __future__ import annotations

from typing import Any


from substrate.kernel.tools import (
    AnyTool,
    ToolExecutionResult,
    is_hosted_tool,
    is_provider_defined_tool,
)
from substrate.kernel.core.content import TextBlock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class SimpleTool:
    name = "simple"
    description = "A simple local tool."
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text="ok")])


class WebSearchHosted:
    name = "web_search"
    description = "Provider-hosted web search."
    provider_specs = {
        "openai": {"type": "web_search_preview", "search_context_size": "medium"},
        "anthropic": {"type": "web_search_20250305", "name": "web_search"},
    }


class LocalShellProviderDefined:
    name = "local_shell"
    description = "Provider-defined local shell execution."
    provider_specs = {
        "openai": {"type": "shell"},
    }
    call_types = ("shell_call",)

    async def handle_call(self, call: dict, *, ctx: Any = None) -> dict:
        return {"type": "shell_output", "output": "done"}


# ---------------------------------------------------------------------------
# TypeGuard tests
# ---------------------------------------------------------------------------


def test_is_hosted_tool_true():
    assert is_hosted_tool(WebSearchHosted())


def test_is_hosted_tool_false_for_local():
    assert not is_hosted_tool(SimpleTool())


def test_is_hosted_tool_false_for_provider_defined():
    assert not is_hosted_tool(LocalShellProviderDefined())


def test_is_provider_defined_tool_true():
    assert is_provider_defined_tool(LocalShellProviderDefined())


def test_is_provider_defined_tool_false_for_local():
    assert not is_provider_defined_tool(SimpleTool())


def test_is_provider_defined_tool_false_for_hosted():
    assert not is_provider_defined_tool(WebSearchHosted())


def test_anytool_accepts_all_kinds():
    tools: list[AnyTool] = [
        SimpleTool(),
        WebSearchHosted(),
        LocalShellProviderDefined(),
    ]
    assert len(tools) == 3
