from pydantic import BaseModel
from typing import Literal, Optional, Any

from ravi.kernel.content import ImageBlock, TextBlock
from ravi.kernel import Tool, ToolResultBlock
from ravi.integrations.mcp.client import MCPClient


class MCPTool(Tool):
    """Adapter that wraps an MCP server tool as a BaseTool.

    This class bridges MCP (Model Context Protocol) tools with the agent
    framework's tool interface. It converts MCP tool schemas to OpenAI
    function calling format and handles tool execution via the MCP client.

    Example:
        ```python
        # Connect to MCP server
        mcp_client = MCPClient()
        await mcp_client.connect(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        )

        # Get available tools from server
        tools_list = await mcp_client.list_tools()

        # Create MCPTool instances
        mcp_tools = [
            MCPTool(
                client=mcp_client,
                name=tool["name"],
                description=tool["description"],
                input_schema=tool["inputSchema"]
            )
            for tool in tools_list
        ]

        # Use with agent
        agent = ReActAgent(
            model_client=client,
            tools=mcp_tools,
            ...
        )
        ```
    """

    def __init__(
        self,
        client: MCPClient,
        name: str,
        description: str,
        input_schema: dict[str, Any],
    ):
        """Initialize MCP tool adapter.

        Args:
            client: Connected MCPClient instance
            name: Tool name from MCP server
            description: Tool description from MCP server
            input_schema: JSON Schema for tool parameters from MCP server
        """
        super().__init__(name=name, description=description, input_schema=input_schema)
        self.client = client

    async def execute(self, **kwargs) -> ToolResultBlock:
        """Execute the MCP tool with given parameters.

        Args:
            **kwargs: Tool parameters matching the input schema

        Returns:
            ToolResultBlock with structured content

        Raises:
            RuntimeError: If MCP client is not connected
        """
        if not self.client.is_connected:
            raise RuntimeError(f"MCP client not connected for tool '{self.name}'")

        try:
            result = await self.client.call_tool(self.name, kwargs)

            if hasattr(result, "content"):
                # Native MCP SDK response
                content = []
                for item in result.content:  # type: ignore[union-attr]
                    if hasattr(item, "type"):
                        if item.type == "text" and hasattr(item, "text"):
                            content.append(TextBlock(text=item.text))
                        elif item.type == "image" and hasattr(item, "data"):
                            mime = getattr(item, "mimeType", "image/png")
                            content.append(ImageBlock(data=item.data, media_type=mime))
                        elif item.type == "resource" and hasattr(item, "resource"):
                            r = item.resource
                            content.append(
                                ResourceBlock(
                                    uri=getattr(r, "uri", ""),
                                    mime_type=getattr(r, "mimeType", None),
                                    text=getattr(r, "text", None),
                                )
                            )
                        else:
                            # Fallback if other types are present
                            content.append(TextBlock(text=str(item)))
                    elif isinstance(item, dict):
                        # Decode from dict
                        item_type = item.get("type", "text")
                        if item_type == "text":
                            content.append(TextBlock(text=str(item.get("text", ""))))
                        elif item_type == "image":
                            content.append(
                                ImageBlock(
                                    data=str(item.get("data", "")),
                                    media_type=str(
                                        item.get(
                                            "mediaType",
                                            item.get("mimeType", "image/png"),
                                        )
                                    ),
                                )
                            )
                        elif item_type == "resource":
                            r = item.get("resource", {})
                            content.append(
                                ResourceBlock(
                                    uri=str(r.get("uri", "")),
                                    mime_type=r.get("mimeType"),
                                    text=r.get("text"),
                                )
                            )
                        else:
                            content.append(TextBlock(text=str(item)))
                    else:
                        # Fallback
                        content.append(TextBlock(text=str(item)))

                is_error: bool = getattr(result, "isError", False)
                return ToolResultBlock(content=content, is_error=is_error)
            else:
                # Legacy: result is a raw string/object
                content = [TextBlock(text=str(result))]
                return ToolResultBlock(content=content, is_error=False)

        except Exception as e:
            return ToolResultBlock(
                content=[TextBlock(text=f"Tool execution failed: {e}")],
                is_error=True,
            )

    @classmethod
    async def from_mcp_client(cls, client: MCPClient) -> list["MCPTool"]:
        """Create MCPTool instances for all tools from an MCP server.

        This is a convenience method to automatically discover and wrap
        all tools from a connected MCP server.

        Args:
            client: Connected MCPClient instance

        Returns:
            List of MCPTool instances, one for each tool on the server

        Raises:
            RuntimeError: If client is not connected

        Example:
            ```python
            mcp_client = MCPClient()
            await mcp_client.connect(command="npx", args=[...])

            # Auto-discover all tools
            tools = await MCPTool.from_mcp_client(mcp_client)

            # Use with agent
            agent = ReActAgent(tools=tools, ...)
            ```
        """
        if not client.is_connected:
            raise RuntimeError("MCP client must be connected before creating tools")

        # List all available tools
        tools_list = await client.list_tools()

        # Create MCPTool instance for each tool
        return [
            cls(
                client=client,
                name=tool["name"],
                description=tool["description"],
                input_schema=tool["inputSchema"],
            )
            for tool in tools_list
        ]
