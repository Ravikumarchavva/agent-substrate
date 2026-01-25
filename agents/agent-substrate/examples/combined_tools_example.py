"""Example: Combining built-in and MCP tools.

This example shows how to use both built-in tools (like Calculator)
and MCP tools (like filesystem) together in a single agent.
"""
import asyncio
import json
from agent_framework.tools import CalculatorTool, GetCurrentTimeTool, MCPClient, MCPTool
from agent_framework.model_clients.openai_client import OpenAIClient
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.messages.agent_messages import (
    UserMessage, SystemMessage, ToolMessage
)


async def main():
    print("🚀 Combined Tools Example\n")
    
    # Built-in tools
    print("🔧 Setting up built-in tools...")
    builtin_tools = [
        CalculatorTool(),
        GetCurrentTimeTool()
    ]
    print(f"✅ Loaded {len(builtin_tools)} built-in tools\n")
    
    # MCP tools
    print("📁 Connecting to MCP server...")
    mcp_client = MCPClient()
    
    try:
        await mcp_client.connect(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
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
        client = OpenAIClient(model="gpt-4o")
        memory = UnboundedMemory()
        
        # System message
        memory.add_message(SystemMessage(
            content="""You are a helpful assistant with access to:
            - Calculator for math operations
            - Current time tool
            - Filesystem tools for reading/writing files
            
            Use these tools to help the user."""
        ))
        
        # User request
        memory.add_message(UserMessage(
            content="Calculate 123 * 456 and save the result to /tmp/calculation.txt"
        ))
        
        # Agent loop
        max_iterations = 5
        for i in range(max_iterations):
            print(f"\n--- Iteration {i + 1} ---")
            
            response = await client.generate(
                messages=memory.get_messages(),
                tools=[t.get_schema() for t in all_tools]
            )
            
            # No tool calls? We're done!
            if not response.tool_calls:
                print(f"✅ Final answer: {response.content}")
                break
            
            # Add assistant message
            memory.add_message(response)
            
            # Execute tool calls
            for tool_call in response.tool_calls:
                tool_name = tool_call.function["name"]
                tool_args = json.loads(tool_call.function["arguments"])
                
                print(f"🔧 Calling: {tool_name}({tool_args})")
                
                # Find and execute tool
                tool = next((t for t in all_tools if t.name == tool_name), None)
                if tool:
                    result = await tool.execute(**tool_args)
                    print(f"   Result: {result[:100]}...")
                    
                    # Add tool result
                    memory.add_message(ToolMessage(
                        content=result,
                        tool_call_id=tool_call.id,
                        name=tool_name
                    ))
        
        print("\n✅ Task completed!")
        
    except Exception as e:
        print(f"❌ Error: {e}")
    
    finally:
        if mcp_client.is_connected:
            await mcp_client.disconnect()
            print("\n✅ Disconnected from MCP server")


if __name__ == "__main__":
    asyncio.run(main())
