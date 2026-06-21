from dotenv import load_dotenv
from substrate.config import SubstrateConfig

load_dotenv()  # walks up to find the repo-root .env
settings = SubstrateConfig()

"""Example: Combining built-in and MCP tools.

This example shows how to use both built-in tools (like Calculator)
and MCP tools (like filesystem) together in a single agent.
"""

import asyncio
from substrate.agents.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from substrate.integrations.tools.mcp import MCPClient, MCPTool
from substrate.integrations.llm.openai.openai_client import OpenAIClient
from substrate.agents.context import InMemoryHistoryProvider
from substrate.kernel.messages.client_messages import (
    UserMessage,
    SystemMessage,
    ToolExecutionResultMessage,
)


async def main():
    print("🚀 Combined Tools Example\n")

    # Built-in tools
    print("🔧 Setting up built-in tools...")
    builtin_tools = [CalculatorTool(), GetCurrentTimeTool()]
    print(f"✅ Loaded {len(builtin_tools)} built-in tools\n")

    # MCP tools
    print("📁 Connecting to MCP server...")
    mcp_client = MCPClient()

    try:
        await mcp_client.connect_stdio(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        )

        mcp_tools = await MCPTool.from_mcp_client(mcp_client)
        print(f"✅ Loaded {len(mcp_tools)} MCP tools\n")

        # Combine all tools
        all_tools = builtin_tools + mcp_tools
        print(f"🎯 Total tools available: {len(all_tools)}")
        print("   Built-in:", [t.name for t in builtin_tools])
        print("   MCP:", [t.name for t in mcp_tools])
        print()

        # Use with agent
        client = OpenAIClient(
            model=settings.CHAT_MODEL.split("/")[-1], api_key=settings.OPENAI_API_KEY
        )
        memory = InMemoryHistoryProvider()

        # System message
        await memory.add_message(
            SystemMessage(
                content="""You are a helpful assistant with access to:
            - Calculator for math operations
            - Current time tool
            - Filesystem tools for reading/writing files
            
            Use these tools to help the user."""
            )
        )

        # User request
        await memory.add_message(
            UserMessage(
                content=[
                    "Calculate 123 * 456 and save the result to /tmp/calculation.txt"
                ]
            )
        )

        # Agent loop
        max_iterations = 5
        for i in range(max_iterations):
            print(f"\n--- Iteration {i + 1} ---")

            response = await client.generate(
                messages=await memory.get_messages(),
                tools=[t.get_openai_schema() for t in all_tools],
            )

            # No tool calls? We're done!
            if not response.tool_calls:
                print(f"✅ Final answer: {response.content}")
                break

            # Add assistant message
            await memory.add_message(response)

            # Execute tool calls
            for tool_call in response.tool_calls:
                tool_name = tool_call.name
                tool_args = tool_call.arguments

                print(f"🔧 Calling: {tool_name}({tool_args})")

                # Find and execute tool
                tool = next((t for t in all_tools if t.name == tool_name), None)
                if tool:
                    result = await tool.execute(**tool_args)
                    first_text = result.content[0].text if result.content else ""
                    print(f"   Result: {first_text[:100]}...")

                    # Add tool result
                    await memory.add_message(
                        ToolExecutionResultMessage.from_tool_result(
                            tool_result=result,
                            tool_call_id=tool_call.id,
                            tool_name=tool_name,
                        )
                    )

        print("\n✅ Task completed!")

    except Exception as e:
        print(f"❌ Error: {e}")

    finally:
        if mcp_client.is_connected:
            await mcp_client.disconnect()
            print("\n✅ Disconnected from MCP server")


if __name__ == "__main__":
    asyncio.run(main())
