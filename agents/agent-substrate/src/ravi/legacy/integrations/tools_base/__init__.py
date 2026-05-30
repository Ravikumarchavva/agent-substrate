"""ravi.kernel.tools — BaseTool contract + ``@tool`` decorator.

Concrete built-in tools (``CalculatorTool``, ``WebSearchTool``, …) live in
:mod:`ravi.fabric.tools`. User-authored tools live in :mod:`ravi.catalog.tools`.
"""

from ravi.kernel.tools.base_tool import (
    BaseTool,
    HitlMode,
    ResettableTool,
    Tool,
    ToolCall,
    ToolResult,
    ToolRisk,
)
from ravi.kernel.tools.parsing import ParsedToolCall, find_tool, parse_tool_call, parse_runtime_tool_payload
from ravi.kernel.tools.approval import tool_needs_approval
from ravi.kernel.tools.functional import tool

__all__ = [
    "BaseTool",
    "HitlMode",
    "ResettableTool",
    "ToolCall",
    "ToolResult",
    "ToolRisk",
    "ParsedToolCall",
    "parse_tool_call",
    "parse_runtime_tool_payload",
    "find_tool",
    "tool_needs_approval",
    "Tool",
    "tool",
]
