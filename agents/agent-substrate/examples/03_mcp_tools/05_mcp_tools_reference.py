"""Example: Using MCP tools with the agent framework.

This example demonstrates how to:
1. Connect to an MCP server
2. Auto-discover available tools
3. Use MCP tools with an agent
"""

import asyncio
from ravi.adapters.mcp.client import MCPClient
from ravi.adapters.mcp.tool import MCPTool
from ravi.adapters.llm.openai.openai_client import OpenAIClient
from ravi.agents.memory.unbounded import UnboundedMemory
from ravi.kernel.messages.client_messages import UserMessage, SystemMessage


async def main():
    print("🚀 MCP Tools Example\n")

    # Connect to MCP filesystem server
    print("📁 Connecting to MCP filesystem server...")
    mcp_client = MCPClient()

    try:
        await mcp_client.connect_stdio(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
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

        from ravi.config import settings

        client = OpenAIClient(
            model=settings.CHAT_MODEL.split("/")[-1], api_key=settings.OPENAI_API_KEY
        )
        memory = UnboundedMemory()

        # Add system message
        await memory.add_message(
            SystemMessage(
                content="You are a helpful assistant with access to filesystem tools."
            )
        )

        # Add user message
        await memory.add_message(
            UserMessage(content=["List the files in the /tmp directory"])
        )

        # Generate response with MCP tools
        response = await client.generate(
            messages=await memory.get_messages(),
            tools=[t.get_openai_schema() for t in mcp_tools],
        )

        print(f"Agent response: {response.content}\n")

        # If agent made tool calls, execute them
        if response.tool_calls:
            print("🔧 Agent requested tool calls:")
            for tool_call in response.tool_calls:
                print(f"   - {tool_call.name}")

    except Exception as e:
        print(f"❌ Error: {e}")

    finally:
        # Cleanup
        if mcp_client.is_connected:
            await mcp_client.disconnect()
            print("\n✅ Disconnected from MCP server")


if __name__ == "__main__":
    asyncio.run(main())
