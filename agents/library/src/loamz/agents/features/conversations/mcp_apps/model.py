from __future__ import annotations

from pydantic import BaseModel

from loamz.agents.kernel.types.content import JsonObject


class McpContextUpdate(BaseModel):
    """POST /threads/{id}/mcp-context: the state an interactive MCP app sends so the model sees it next turn.

    `context` is app-specific (board state, playback, a selected colour); it is serialised as JSON for the model.
    """

    tool_name: str
    context: JsonObject


class McpAppEntry(BaseModel):
    tool_name: str
    description: str = ""
    resource_uri: str
    http_url: str
    permissions: list[str] = []
    prefers_border: bool = False


class McpManifest(BaseModel):
    items: list[McpAppEntry]
