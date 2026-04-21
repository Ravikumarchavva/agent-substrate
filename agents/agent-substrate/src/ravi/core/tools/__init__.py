"""core.tools - BaseTool contract, built-in tools, and CapabilityRegistry."""

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
)
from ravi.core.tools.catalog import CapabilityRegistry

__all__ = [
    "BaseTool",
    "HitlMode",
    "ToolCall",
    "ToolResult",
    "ToolRisk",
    "Tool",
    "CalculatorTool",
    "GetCurrentTimeTool",
    "WebSearchTool",
    "CapabilityRegistry",
]
