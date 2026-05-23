"""core.tools - BaseTool contract and built-in tools."""

from ravi.core.tools.base_tool import (
    BaseTool,
    HitlMode,
    ToolCall,
    ToolResult,
    ToolRisk,
    Tool,
)
from ravi.core.tools.builtin_tools import (
    CalculatorTool,
    GetCurrentTimeTool,
    WebSearchTool,
    GetBitcoinPriceTool,
)
from ravi.core.tools.functional import tool

__all__ = [
    "BaseTool",
    "HitlMode",
    "ToolCall",
    "ToolResult",
    "ToolRisk",
    "Tool",
    "tool",
    "CalculatorTool",
    "GetCurrentTimeTool",
    "WebSearchTool",
    "GetBitcoinPriceTool",
]

