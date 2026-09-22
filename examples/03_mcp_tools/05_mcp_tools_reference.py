from dotenv import load_dotenv
from substrate.config import SubstrateConfig

load_dotenv()  # walks up to find the repo-root .env
settings = SubstrateConfig()

"""Example: Using MCP tools with the agent framework.

This example demonstrates how to:
1. Connect to an MCP server
2. Auto-discover available tools
3. Use MCP tools with an agent
"""

import asyncio
from substrate.integrations.tools.mcp.client import MCPClient
from substrate.integrations.tools.mcp.tool import MCPTool
from substrate.integrations.llm.openai.openai_client import OpenAIClient
from substrate.agents.storage import LocalFilesystemHistoryProvider, project_messages
from substrate.kernel.core.content import ChatMessage, Role, TextBlock, ToolUseBlock
from substrate.kernel.llm.llm import GenerationOptions
from substrate.kernel.storage.history import MessageNode


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
        client = OpenAIClient(
            model=settings.CHAT_MODEL.split("/")[-1], api_key=settings.OPENAI_API_KEY
        )
        session_id = "mcp-tools-demo"
        memory = LocalFilesystemHistoryProvider()

        # Record the user's turn as a DAG node (the system prompt goes on
        # GenerationOptions below, not into history — see ReActAgent for the
        # same split between stored conversation turns and per-call config).
        user_node = MessageNode(
            session_id=session_id,
            parent_id=None,
            payload=ChatMessage(
                role=Role.USER,
                content=[TextBlock(text="List the files in the /tmp directory")],
            ),
        )
        await memory.append_and_advance(user_node, "main", expected_head_id=None)

        # Generate response with MCP tools — the client converts `tools` to
        # its own vendor wire-format internally (see GenerationOptions).
        messages = await project_messages(memory, session_id)
        response = await client.generate(
            messages,
            options=GenerationOptions(
                tools=mcp_tools,
                system_instructions="You are a helpful assistant with access to filesystem tools.",
            ),
        )

        print(f"Agent response: {response.text}\n")

        # If the agent requested tool calls, they show up as ToolUseBlocks.
        tool_calls = [b for b in response.content if isinstance(b, ToolUseBlock)]
        if tool_calls:
            print("🔧 Agent requested tool calls:")
            for call in tool_calls:
                print(f"   - {call.tool_name}")

        await memory.delete_session(session_id)

    except Exception as e:
        print(f"❌ Error: {e}")

    finally:
        # Cleanup
        if mcp_client.is_connected:
            await mcp_client.disconnect()
            print("\n✅ Disconnected from MCP server")


if __name__ == "__main__":
    asyncio.run(main())
