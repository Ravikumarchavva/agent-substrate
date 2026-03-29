"""Tests for ChainRuntime — LLM-written script execution."""

from __future__ import annotations

import pytest

from raavan.catalog._chain_runtime import (
    AdapterProxy,
    ChainRuntime,
    _AdapterNamespace,
)
from raavan.core.tools.base_tool import BaseTool, ToolResult
from raavan.core.tools.catalog import CapabilityRegistry


class _AddTool(BaseTool):
    """Simple tool for testing chains."""

    def __init__(self) -> None:
        super().__init__(
            name="add",
            description="Add two numbers",
            input_schema={
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
            },
        )

    async def execute(self, a: float = 0, b: float = 0, **kwargs) -> ToolResult:
        return ToolResult(content=[{"type": "text", "text": str(a + b)}])


class _EchoTool(BaseTool):
    """Echoes input for testing."""

    def __init__(self) -> None:
        super().__init__(
            name="echo",
            description="Echo input",
            input_schema={
                "type": "object",
                "properties": {"text": {"type": "string"}},
            },
        )

    async def execute(self, text: str = "", **kwargs) -> ToolResult:
        return ToolResult(content=[{"type": "text", "text": text}])


@pytest.fixture
def catalog() -> CapabilityRegistry:
    cat = CapabilityRegistry()
    cat.register_tool(_AddTool(), category="test", tags=["math"])
    cat.register_tool(_EchoTool(), category="test", tags=["text"])
    return cat


class TestAdapterProxy:
    """AdapterProxy wraps tool execution."""

    async def test_proxy_call(self) -> None:
        tool = _AddTool()
        proxy = AdapterProxy(tool)
        result = await proxy(a=3, b=4)
        assert "7" in str(result)

    async def test_proxy_echo(self) -> None:
        tool = _EchoTool()
        proxy = AdapterProxy(tool)
        result = await proxy(text="hello")
        assert "hello" in str(result)


class TestAdapterNamespace:
    """_AdapterNamespace provides attribute-style access."""

    def test_register_and_access(self) -> None:
        ns = _AdapterNamespace()
        proxy = AdapterProxy(_AddTool())
        ns.register(proxy)
        assert ns.add is proxy

    def test_missing_raises_attribute_error(self) -> None:
        ns = _AdapterNamespace()
        with pytest.raises(AttributeError, match="No adapter named"):
            _ = ns.nonexistent

    def test_list_adapters(self) -> None:
        ns = _AdapterNamespace()
        ns.register(AdapterProxy(_AddTool()))
        ns.register(AdapterProxy(_EchoTool()))
        assert ns.list_adapters() == ["add", "echo"]


class TestChainRuntime:
    """ChainRuntime script execution tests."""

    async def test_simple_script(self, catalog: CapabilityRegistry) -> None:
        runtime = ChainRuntime(catalog=catalog)
        result = await runtime.execute_script(
            "r = await adapters.add(a=10, b=20)\nresults.append(r)"
        )
        assert result.error is None
        assert len(result.outputs) == 1
        assert result.duration_ms > 0

    async def test_chained_calls(self, catalog: CapabilityRegistry) -> None:
        runtime = ChainRuntime(catalog=catalog)
        result = await runtime.execute_script(
            "r1 = await adapters.add(a=1, b=2)\n"
            "r2 = await adapters.echo(text=f'sum={r1}')\n"
            "results.append(r2)"
        )
        assert result.error is None
        assert "sum=" in str(result.outputs[0])

    async def test_print_captured(self, catalog: CapabilityRegistry) -> None:
        runtime = ChainRuntime(catalog=catalog)
        result = await runtime.execute_script(
            "print('hello from chain')\nresults.append('done')"
        )
        assert "hello from chain" in result.logs

    async def test_timeout(self, catalog: CapabilityRegistry) -> None:
        runtime = ChainRuntime(catalog=catalog)
        result = await runtime.execute_script(
            "import asyncio\nawait asyncio.sleep(10)",
            timeout=1,
        )
        assert result.error is not None
        assert "timed out" in result.error.lower()

    async def test_error_captured(self, catalog: CapabilityRegistry) -> None:
        runtime = ChainRuntime(catalog=catalog)
        result = await runtime.execute_script("raise ValueError('test error')")
        assert result.error is not None
        assert "test error" in result.error

    async def test_missing_adapter_error(self, catalog: CapabilityRegistry) -> None:
        runtime = ChainRuntime(catalog=catalog)
        result = await runtime.execute_script("await adapters.nonexistent()")
        assert result.error is not None
