"""Functional tool decorator — turn any async function into a ``BaseTool``.

Usage::

    from ravi.core.tools import tool

    @tool
    async def get_weather(location: str) -> str:
        \"\"\"Get the current weather for a location.\"\"\"
        return f"Sunny in {location}"

    # get_weather is now a BaseTool instance ready to pass to an agent:
    agent = RuntimeAssistantAgent(..., tools=[get_weather])

The decorator:
- Extracts name from the function name.
- Extracts description from the function docstring.
- Builds input_schema automatically from type annotations.
- Wraps the function call inside ``execute(**kwargs) -> ToolResult``.
- Preserves ALL BaseTool contracts (risk, hitl, saga, locking, schema).
"""

from __future__ import annotations

import inspect
import logging
from typing import Any, Callable, Dict, List, Optional, get_type_hints

from ravi.core.tools.base_tool import BaseTool, ToolResult, ToolRisk
from ravi.core.messages.content import TextBlock

logger = logging.getLogger("ravi.core.tools.functional")

# JSON Schema type mapping from Python types
_TYPE_MAP: Dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _python_type_to_json_schema(annotation: Any) -> str:
    """Map a Python type annotation to a JSON Schema type string."""
    return _TYPE_MAP.get(annotation, "string")


def _build_schema_from_function(fn: Callable) -> Dict[str, Any]:
    """Inspect function signature and build a JSON Schema input_schema."""
    sig = inspect.signature(fn)
    hints = get_type_hints(fn)

    properties: Dict[str, Any] = {}
    required: List[str] = []

    for param_name, param in sig.parameters.items():
        if param_name in ("self", "cls", "kwargs"):
            continue

        param_type = hints.get(param_name, str)

        # Skip return type
        if param_name == "return":
            continue

        prop: Dict[str, Any] = {
            "type": _python_type_to_json_schema(param_type),
        }

        # Use parameter default as description hint if no other info
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

        properties[param_name] = prop

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


class _FunctionalTool(BaseTool):
    """Internal BaseTool subclass wrapping a plain async function."""

    def __init__(
        self,
        fn: Callable,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        risk: ToolRisk = ToolRisk.SAFE,
    ) -> None:
        tool_name = name or fn.__name__
        tool_desc = description or fn.__doc__ or f"Tool: {tool_name}"
        # Strip leading whitespace from docstrings
        tool_desc = inspect.cleandoc(tool_desc)
        input_schema = _build_schema_from_function(fn)

        super().__init__(
            name=tool_name,
            description=tool_desc,
            input_schema=input_schema,
            risk=risk,
        )
        self._fn = fn

    async def execute(self, **kwargs: Any) -> ToolResult:
        """Call the wrapped function and coerce its return to ToolResult."""
        result = self._fn(**kwargs)
        # Support both sync and async functions
        if inspect.isawaitable(result):
            result = await result

        # Coerce return value to ToolResult
        if isinstance(result, ToolResult):
            return result
        return ToolResult(content=[TextBlock(text=str(result))])


def tool(
    fn: Optional[Callable] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    risk: ToolRisk = ToolRisk.SAFE,
) -> BaseTool | Callable[..., BaseTool]:
    """Decorator to convert a function into a ``BaseTool``.

    Can be used with or without arguments::

        @tool
        async def my_tool(x: str) -> str: ...

        @tool(name="custom_name", risk=ToolRisk.SENSITIVE)
        async def my_tool(x: str) -> str: ...
    """
    if fn is not None:
        # Called as @tool without parentheses
        return _FunctionalTool(fn)

    # Called as @tool(...) with arguments
    def wrapper(fn: Callable) -> BaseTool:
        return _FunctionalTool(fn, name=name, description=description, risk=risk)
    return wrapper
