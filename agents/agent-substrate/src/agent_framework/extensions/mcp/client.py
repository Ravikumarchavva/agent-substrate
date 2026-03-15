"""MCP (Model Context Protocol) client for connecting to MCP servers."""
from typing import Any, Optional, Literal
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client


class MCPClient:
    """Client for connecting to and interacting with MCP servers.
    
    MCP (Model Context Protocol) is an open protocol that enables AI systems
    to connect to external tools and data sources. This client manages the
    connection to MCP servers and facilitates tool discovery and execution.
    
    Supports both stdio and SSE (Server-Sent Events) transports.
    
    Example (stdio):
        ```python
        # Connect to an MCP server via stdio
        client = MCPClient()
        await client.connect_stdio(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        )
        
        # Discover available tools
        tools = await client.list_tools()
        
        # Execute a tool
        result = await client.call_tool("read_file", {"path": "/tmp/test.txt"})
        
        # Cleanup
        await client.disconnect()
        ```
    
    Example (SSE):
        ```python
        # Connect to an MCP server via SSE (HTTP)
        client = MCPClient()
        await client.connect_sse(
            url="http://localhost:8000/sse"
        )
        
        # Use tools same as stdio
        tools = await client.list_tools()
        ```
    """
    
    def __init__(self):
        """Initialize MCP client."""
        self.session: Optional[ClientSession] = None
        self.exit_stack: Optional[AsyncExitStack] = None
        self._connected = False
        self._transport_type: Optional[Literal["stdio", "sse"]] = None
    
    async def connect_stdio(
        self,
        command: str,
        args: list[str],
        env: Optional[dict[str, str]] = None
    ) -> None:
        """Connect to an MCP server via stdio transport.
        
        Args:
            command: Command to start the MCP server (e.g., "npx", "python")
            args: Arguments for the command
            env: Optional environment variables for the server process
            
        Raises:
            RuntimeError: If already connected or connection fails
        """
        if self._connected:
            raise RuntimeError("Already connected to an MCP server")
        
        try:
            self.exit_stack = AsyncExitStack()
            
            # Create server parameters
            server_params = StdioServerParameters(
                command=command,
                args=args,
                env=env
            )
            
            # Connect to server
            stdio_transport = await self.exit_stack.enter_async_context(
                stdio_client(server_params)
            )
            
            # Create session
            read_stream, write_stream = stdio_transport
            self.session = await self.exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            
            # Initialize session
            await self.session.initialize()
            
            self._connected = True
            self._transport_type = "stdio"
            
        except Exception as e:
            if self.exit_stack:
                await self.exit_stack.aclose()
            raise RuntimeError(f"Failed to connect to MCP server via stdio: {e}") from e
    
    async def connect_sse(
        self,
        url: str,
        headers: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None
    ) -> None:
        """Connect to an MCP server via SSE (Server-Sent Events) transport.
        
        Args:
            url: URL of the SSE endpoint (e.g., "http://localhost:8000/sse")
            headers: Optional HTTP headers for authentication, etc.
            timeout: Optional timeout for the connection in seconds
            
        Raises:
            RuntimeError: If already connected or connection fails
        """
        if self._connected:
            raise RuntimeError("Already connected to an MCP server")
        
        try:
            self.exit_stack = AsyncExitStack()
            
            # Connect to SSE server
            sse_transport = await self.exit_stack.enter_async_context(
                sse_client(url, headers=headers, timeout=timeout)
            )
            
            # Create session
            read_stream, write_stream = sse_transport
            self.session = await self.exit_stack.enter_async_context(
                ClientSession(read_stream, write_stream)
            )
            
            # Initialize session
            await self.session.initialize()
            
            self._connected = True
            self._transport_type = "sse"
            
        except Exception as e:
            if self.exit_stack:
                await self.exit_stack.aclose()
            raise RuntimeError(f"Failed to connect to MCP server via SSE: {e}") from e
    
    async def connect(
        self,
        command: str,
        args: list[str],
        env: Optional[dict[str, str]] = None
    ) -> None:
        """Connect to an MCP server via stdio transport (backward compatibility).
        
        This method is kept for backward compatibility. Use connect_stdio() instead.
        
        Args:
            command: Command to start the MCP server (e.g., "npx", "python")
            args: Arguments for the command
            env: Optional environment variables for the server process
            
        Raises:
            RuntimeError: If already connected or connection fails
        """
        await self.connect_stdio(command, args, env)
    
    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        if self.exit_stack:
            await self.exit_stack.aclose()
        self.session = None
        self.exit_stack = None
        self._connected = False
        self._transport_type = None
    
    @property
    def is_connected(self) -> bool:
        """Check if connected to an MCP server."""
        return self._connected
    
    @property
    def transport_type(self) -> Optional[Literal["stdio", "sse"]]:
        """Get the current transport type (stdio or sse)."""
        return self._transport_type
    
    async def list_tools(self) -> list[dict[str, Any]]:
        """List all available tools from the MCP server.
        
        Returns:
            List of tool definitions with name, description, and input schema
            
        Raises:
            RuntimeError: If not connected to a server
        """
        if not self._connected or not self.session:
            raise RuntimeError("Not connected to an MCP server")
        
        try:
            response = await self.session.list_tools()
            return [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema
                }
                for tool in response.tools
            ]
        except Exception as e:
            raise RuntimeError(f"Failed to list tools: {e}") from e
    
    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any]
    ) -> str:
        """Execute a tool on the MCP server.
        
        Args:
            name: Name of the tool to execute
            arguments: Tool arguments as a dictionary
            
        Returns:
            Tool execution result as a JSON string
            
        Raises:
            RuntimeError: If not connected or tool execution fails
        """
        if not self._connected or not self.session:
            raise RuntimeError("Not connected to an MCP server")
        
        try:
            response = await self.session.call_tool(name, arguments)
            return response
            
        except Exception as e:
            raise RuntimeError(f"Tool execution failed for '{name}': {e}") from e
    
    async def list_resources(self) -> list[dict[str, Any]]:
        """List all available resources from the MCP server.
        
        Returns:
            List of resource definitions
            
        Raises:
            RuntimeError: If not connected to a server
        """
        if not self._connected or not self.session:
            raise RuntimeError("Not connected to an MCP server")
        
        try:
            response = await self.session.list_resources()
            return [
                {
                    "uri": resource.uri,
                    "name": resource.name,
                    "description": resource.description or "",
                    "mimeType": resource.mimeType if hasattr(resource, "mimeType") else None
                }
                for resource in response.resources
            ]
        except Exception as e:
            raise RuntimeError(f"Failed to list resources: {e}") from e
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()
