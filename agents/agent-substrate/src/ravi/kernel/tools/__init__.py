"""ravi.kernel.tools — BaseTool contract + ``@tool`` decorator.

Concrete built-in tools (``CalculatorTool``, ``WebSearchTool``, …) live in
:mod:`ravi.extensions.tools`. User-authored tools live in :mod:`ravi.catalog.tools`.
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
from ravi.kernel.tools.functional import tool

__all__ = [
    "BaseTool",
    "HitlMode",
    "ResettableTool",
    "ToolCall",
    "ToolResult",
    "ToolRisk",
    "Tool",
    "tool",
]
