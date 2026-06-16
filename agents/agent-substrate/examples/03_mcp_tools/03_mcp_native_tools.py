"""03-3 — MCPTool deep dive: schema inspection and direct execution

Demonstrates the MCPTool adapter API in detail:
  - Both schema formats: get_mcp_schema() (MCP wire format) and get_openai_schema()
  - Direct tool execution via tool.run(**kwargs)
  - Error handling when a tool call fails

Prerequisites:
  - Node.js / npx on PATH
"""

import asyncio
import json

from ravi.integrations.tools.mcp.client import MCPClient

# Infrastructure: Node.js / npx required to launch the MCP filesystem server.


async def main() -> None:
    # --- Connect to MCP server ---
    mcp_client = MCPClient()
    try:
        await mcp_client.connect_stdio(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        )
        print(f"Connected via {mcp_client.transport_type} transport")

        mcp_tools = await mcp_client.discover_tools()
        print(f"Discovered {len(mcp_tools)} tools\n")

        # --- Inspect tool schemas ---
        for tool in mcp_tools[:2]:
            print(f"Tool: {tool.name}")
            print(f"  Description: {tool.description}")
            print("  MCP wire format:")
            print(json.dumps(tool.get_mcp_schema(), indent=4))
            print("  OpenAI function-calling format:")
            print(json.dumps(tool.get_openai_schema(), indent=4))
            print()

        # --- Execute a tool directly ---
        list_tool = next(
            (t for t in mcp_tools if "list" in t.name.lower()), mcp_tools[0]
        )
        print(f"Executing: {list_tool.name}(path='/tmp')")
        result = await list_tool.run(path="/tmp")
        first_text = result.content[0].text if result.content else ""
        print(f"Result (first 300 chars): {first_text[:300]}")
        print(f"is_error: {result.is_error}")

        # --- Error handling: bad path ---
        print(f"\nExecuting: {list_tool.name}(path='/nonexistent_path_xyz')")
        error_result = await list_tool.run(path="/nonexistent_path_xyz")
        print(f"is_error: {error_result.is_error}")
        if error_result.content:
            print(f"Error content: {error_result.content[0].text[:200]}")

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if mcp_client.is_connected:
            await mcp_client.disconnect()
            print("\nDisconnected from MCP server")


if __name__ == "__main__":
    asyncio.run(main())
