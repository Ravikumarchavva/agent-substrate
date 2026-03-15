"""MCP tool adapter that wraps MCP server tools as BaseTool instances."""
from typing import Any
import json

from .base_tool import BaseTool, ToolResult
from .mcp_client import MCPClient


class MCPTool(BaseTool):
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
        input_schema: dict[str, Any]
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
    
    async def execute(self, **kwargs) -> ToolResult:
        """Execute the MCP tool with given parameters.
        
        Args:
            **kwargs: Tool parameters matching the input schema
            
        Returns:
            ToolResult with structured content
            
        Raises:
            RuntimeError: If MCP client is not connected
        """
        if not self.client.is_connected:
            raise RuntimeError(f"MCP client not connected for tool '{self.name}'")
        
        try:
            # Call tool via MCP client - returns MCP response object
            result = await self.client.call_tool(self.name, kwargs)
            
            # MCP responses come back as CallToolResult objects with content list
            # Convert to our ToolResult format
            if hasattr(result, 'content'):
                # Native MCP SDK response
                content = []
                for item in result.content:
                    if hasattr(item, 'type') and hasattr(item, 'text'):
                        # TextContent object
                        content.append({"type": "text", "text": item.text})
                    elif isinstance(item, dict):
                        # Already a dict
                        content.append(item)
                    else:
                        # Fallback
                        content.append({"type": "text", "text": str(item)})
                
                is_error = getattr(result, 'isError', False)
                return ToolResult(content=content, isError=is_error)
            else:
                # Legacy: result is a raw string/object
                content = [{"type": "text", "text": str(result)}]
                return ToolResult(content=content, isError=False)
                
        except Exception as e:
            # Wrap execution errors
            return ToolResult(
                content=[{"type": "text", "text": f"Tool execution failed: {str(e)}"}],
                isError=True
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
                input_schema=tool["inputSchema"]
            )
            for tool in tools_list
        ]
