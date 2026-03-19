"""core.tools - BaseTool contract, built-in tools, and ToolRegistry."""

from agent_framework.core.tools.base_tool import BaseTool, HitlMode, ToolCall, ToolResult, ToolRisk, Tool
from agent_framework.core.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool, WebSearchTool
from agent_framework.core.tools.registry import ToolRegistry

__all__ = [
    "BaseTool", "HitlMode", "ToolCall", "ToolResult", "ToolRisk", "Tool",
    "CalculatorTool", "GetCurrentTimeTool", "WebSearchTool",
    "ToolRegistry",
]
