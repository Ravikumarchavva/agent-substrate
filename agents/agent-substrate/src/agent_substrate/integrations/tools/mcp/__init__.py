"""agent_substrate.integrations.tools.mcp — Model Context Protocol client and tool wrapper."""

from __future__ import annotations


from agent_substrate.integrations.tools.mcp.client import MCPClient
from agent_substrate.integrations.tools.mcp.tool import MCPTool

__all__ = [
    "MCPClient",
    "MCPTool",
]
