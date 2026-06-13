"""Tests for the kernel tool taxonomy: ToolSpec, TypeGuards, spec_of."""

from __future__ import annotations

from typing import Any


from ravi.kernel.tools import (
    AnyTool,
    FunctionSpec,
    ProviderSpec,
    ToolExecutionResult,
    ToolSpec,
    is_hosted_tool,
    is_provider_defined_tool,
    spec_of,
)
from ravi.kernel.core.content import TextBlock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class SimpleTool:
    name = "simple"
    description = "A simple local tool."
    input_schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, *, ctx: Any = None, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text="ok")])


class DeferredTool:
    name = "deferred"
    description = "A deferred-loading tool."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"q": {"type": "string"}},
    }
    lazy_schema = True

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


# ---------------------------------------------------------------------------
# spec_of tests
# ---------------------------------------------------------------------------


def test_spec_of_local_tool_returns_function_spec():
    tool = SimpleTool()
    spec = spec_of(tool, provider="openai")
    assert isinstance(spec, FunctionSpec)
    assert spec.kind == "function"
    assert spec.name == "simple"
    assert spec.description == "A simple local tool."
    assert spec.lazy_schema is False


def test_spec_of_deferred_tool():
    tool = DeferredTool()
    spec = spec_of(tool, provider="openai")
    assert isinstance(spec, FunctionSpec)
    assert spec.lazy_schema is True


def test_spec_of_hosted_known_provider():
    tool = WebSearchHosted()
    spec = spec_of(tool, provider="openai")
    assert isinstance(spec, ProviderSpec)
    assert spec.kind == "provider"
    assert spec.provider == "openai"
    assert spec.spec == {"type": "web_search_preview", "search_context_size": "medium"}


def test_spec_of_hosted_anthropic():
    tool = WebSearchHosted()
    spec = spec_of(tool, provider="anthropic")
    assert isinstance(spec, ProviderSpec)
    assert spec.spec["type"] == "web_search_20250305"


def test_spec_of_hosted_unknown_provider_returns_none():
    tool = WebSearchHosted()
    assert spec_of(tool, provider="gemini") is None


def test_spec_of_provider_defined_known_provider():
    tool = LocalShellProviderDefined()
    spec = spec_of(tool, provider="openai")
    assert isinstance(spec, ProviderSpec)
    assert spec.spec == {"type": "shell"}


def test_spec_of_provider_defined_unknown_provider_returns_none():
    tool = LocalShellProviderDefined()
    assert spec_of(tool, provider="anthropic") is None


# ---------------------------------------------------------------------------
# ToolSpec JSON round-trip
# ---------------------------------------------------------------------------


def test_function_spec_json_round_trip():
    spec = FunctionSpec(
        name="my_tool",
        description="A tool",
        parameters={"type": "object", "properties": {}},
        lazy_schema=True,
        strict=False,
    )
    raw = spec.model_dump()
    reconstructed = FunctionSpec.model_validate(raw)
    assert reconstructed == spec


def test_provider_spec_json_round_trip():
    spec = ProviderSpec(
        name="web_search",
        provider="openai",
        spec={"type": "web_search_preview"},
    )
    raw = spec.model_dump()
    reconstructed = ProviderSpec.model_validate(raw)
    assert reconstructed == spec


def test_tool_spec_discriminated_union():
    from pydantic import TypeAdapter

    adapter: TypeAdapter[ToolSpec] = TypeAdapter(ToolSpec)
    fn = adapter.validate_python({"kind": "function", "name": "t", "parameters": {}})
    assert isinstance(fn, FunctionSpec)
    prov = adapter.validate_python(
        {"kind": "provider", "spec": {"type": "x"}, "provider": "openai"}
    )
    assert isinstance(prov, ProviderSpec)
