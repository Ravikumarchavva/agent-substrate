"""core.tools - BaseTool contract and built-in lightweight tools."""

from agent_framework.core.tools.base_tool import BaseTool, HitlMode, ToolCall, ToolResult, ToolRisk, Tool
from agent_framework.core.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool, WebSearchTool

__all__ = [
    "BaseTool", "HitlMode", "ToolCall", "ToolResult", "ToolRisk", "Tool",
    "CalculatorTool", "GetCurrentTimeTool", "WebSearchTool",
]
