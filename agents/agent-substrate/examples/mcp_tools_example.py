"""Example: Using MCP tools with the agent framework.

This example demonstrates how to:
1. Connect to an MCP server
2. Auto-discover available tools
3. Use MCP tools with an agent
"""
import asyncio
from agent_framework.tools import MCPClient, MCPTool
from agent_framework.model_clients.openai_client import OpenAIClient
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.messages.agent_messages import UserMessage, SystemMessage


async def main():
    print("🚀 MCP Tools Example\n")
    
    # Connect to MCP filesystem server
    print("📁 Connecting to MCP filesystem server...")
    mcp_client = MCPClient()
    
    try:
        await mcp_client.connect(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        )
        print("✅ Connected to MCP server\n")
        
        # Discover available tools
        print("🔍 Discovering available tools...")
        mcp_tools = await MCPTool.from_mcp_client(mcp_client)
        
        print(f"✅ Found {len(mcp_tools)} tools:")
        for tool in mcp_tools:
            print(f"   - {tool.name}: {tool.description}")
        print()
        
        # Example: Use with OpenAI client
        print("🤖 Using MCP tools with agent...\n")
        
        client = OpenAIClient(model="gpt-4o")
        memory = UnboundedMemory()
        
        # Add system message
        memory.add_message(SystemMessage(
            content="You are a helpful assistant with access to filesystem tools."
        ))
        
        # Add user message
        memory.add_message(UserMessage(
            content="List the files in the /tmp directory"
        ))
        
        # Generate response with MCP tools
        response = await client.generate(
            messages=memory.get_messages(),
            tools=[t.get_schema() for t in mcp_tools]
        )
        
        print(f"Agent response: {response.content}\n")
        
        # If agent made tool calls, execute them
        if response.tool_calls:
            print("🔧 Agent requested tool calls:")
            for tool_call in response.tool_calls:
                print(f"   - {tool_call.function['name']}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
    
    finally:
        # Cleanup
        if mcp_client.is_connected:
            await mcp_client.disconnect()
            print("\n✅ Disconnected from MCP server")


if __name__ == "__main__":
    asyncio.run(main())
