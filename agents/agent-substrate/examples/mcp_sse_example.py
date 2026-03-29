"""Example: Using MCP with SSE (Server-Sent Events) transport.

This example demonstrates how to connect to an MCP server via HTTP/SSE
instead of stdio. This is useful for:
- Connecting to remote MCP servers
- Sharing one MCP server across multiple clients
- Production deployments where servers run independently
"""
import asyncio
from raavan.integrations.mcp import MCPClient, MCPTool
from raavan.integrations.llm.openai.openai_client import OpenAIClient
from raavan.core.memory.unbounded_memory import UnboundedMemory
from raavan.core.messages.client_messages import UserMessage, SystemMessage


async def main():
    print("🚀 MCP SSE Transport Example\n")
    
    # Connect to MCP server via SSE (HTTP)
    print("🌐 Connecting to MCP server via SSE...")
    mcp_client = MCPClient()
    
    try:
        # Example: Connect to a running MCP server
        # Note: You need to have an MCP server running on this endpoint
        await mcp_client.connect_sse(
            url="http://localhost:8000/sse",
            headers={
                # Optional: Add authentication headers
                # "Authorization": "Bearer your-api-token",
                # "X-API-Key": "your-api-key"
            },
            timeout=30.0  # Connection timeout in seconds
        )
        
        print(f"✅ Connected via {mcp_client.transport_type} transport\n")
        
        # Discover available tools
        print("🔍 Discovering available tools...")
        mcp_tools = await MCPTool.from_mcp_client(mcp_client)
        
        print(f"✅ Found {len(mcp_tools)} tools:")
        for tool in mcp_tools:
            print(f"   - {tool.name}: {tool.description}")
        print()
        
        # Use with agent
        print("🤖 Using MCP tools with agent...\n")
        
        client = OpenAIClient(model="gpt-4o")
        memory = UnboundedMemory()
        
        memory.add_message(SystemMessage(
            content="You are a helpful assistant with access to tools via MCP."
        ))
        
        memory.add_message(UserMessage(
            content="Use the available tools to help me"
        ))
        
        response = await client.generate(
            messages=memory.get_messages(),
            tools=[t.get_schema() for t in mcp_tools]
        )
        
        print(f"Agent response: {response.content}\n")
        
    except RuntimeError as e:
        print(f"❌ Connection error: {e}")
        print("\n💡 Tips:")
        print("   - Make sure an MCP server is running on the specified URL")
        print("   - Check that the server supports SSE transport")
        print("   - Verify authentication headers if required")
    
    except Exception as e:
        print(f"❌ Error: {e}")
    
    finally:
        # Cleanup
        if mcp_client.is_connected:
            await mcp_client.disconnect()
            print("\n✅ Disconnected from MCP server")


async def compare_transports():
    """Compare stdio vs SSE transports."""
    print("\n" + "="*60)
    print("📊 Comparing Stdio vs SSE Transports")
    print("="*60 + "\n")
    
    # Stdio transport
    print("1️⃣  Stdio Transport (Process-based)")
    print("   ✓ Launches server as subprocess")
    print("   ✓ Automatic lifecycle management")
    print("   ✓ Good for local development")
    print("   ✗ One client per server instance")
    print()
    
    stdio_client = MCPClient()
    try:
        await stdio_client.connect_stdio(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        )
        print(f"   Connected via: {stdio_client.transport_type}")
        await stdio_client.disconnect()
    except Exception as e:
        print(f"   Error: {e}")
    
    print()
    
    # SSE transport
    print("2️⃣  SSE Transport (HTTP-based)")
    print("   ✓ Connects to running server")
    print("   ✓ Multiple clients can share one server")
    print("   ✓ Good for production/remote servers")
    print("   ✓ Supports authentication")
    print()
    
    sse_client = MCPClient()
    try:
        await sse_client.connect_sse(
            url="http://localhost:8000/sse"
        )
        print(f"   Connected via: {sse_client.transport_type}")
        await sse_client.disconnect()
    except Exception as e:
        print(f"   Note: {e}")
        print("   (This is expected if no SSE server is running)")


if __name__ == "__main__":
    print("="*60)
    print("MCP SSE Transport Example")
    print("="*60 + "\n")
    
    # Run main example
    asyncio.run(main())
    
    # Compare transports
    asyncio.run(compare_transports())
    
    print("\n" + "="*60)
    print("💡 To use SSE transport:")
    print("   1. Start an MCP server with SSE support")
    print("   2. Update the URL in this script")
    print("   3. Run this example again")
    print("="*60)
