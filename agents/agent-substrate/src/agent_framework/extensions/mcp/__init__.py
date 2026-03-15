"""extensions.mcp - Model Context Protocol client, tool wrapper, and App UIs."""

from agent_framework.extensions.mcp.client import MCPClient
from agent_framework.extensions.mcp.tool import MCPTool
from agent_framework.extensions.mcp.app_tools import (
    DataVisualizerTool,
    MarkdownPreviewerTool,
    JsonExplorerTool,
    ColorPaletteTool,
    KanbanBoardTool,
    SpotifyPlayerTool,
)

__all__ = [
    "MCPClient",
    "MCPTool",
    "DataVisualizerTool",
    "MarkdownPreviewerTool",
    "JsonExplorerTool",
    "ColorPaletteTool",
    "KanbanBoardTool",
    "SpotifyPlayerTool",
]
