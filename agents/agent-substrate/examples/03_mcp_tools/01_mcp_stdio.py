"""03-1 — MCP Tools via stdio transport

Connects to an MCP filesystem server launched as a subprocess (stdio transport),
auto-discovers its tools, sends a user message to an LLM with the tool schemas,
and executes any tool calls the model requests.

Prerequisites:
  - Node.js / npx on PATH
  - OPENAI_API_KEY (or whichever model is configured via CHAT_MODEL)
"""

import asyncio
import json

from ravi.config import settings
from ravi.integrations.llm.factory import create_model_client
from ravi.integrations.tools.mcp.client import MCPClient
from ravi.kernel.messages.client_messages import (
    SystemMessage,
    ToolExecutionResultMessage,
    UserMessage,
)
from ravi.kernel.messages.content import TextBlock

# Infrastructure: Node.js / npx required to launch the MCP filesystem server.

CHAT_MODEL = settings.CHAT_MODEL
API_KEYS = {
    "openai": settings.OPENAI_API_KEY,
    "anthropic": settings.ANTHROPIC_API_KEY,
    "google": settings.GEMINI_API_KEY,
    "groq": settings.GROQ_API_KEY,
    "openrouter": settings.OPENROUTER_API_KEY,
}


async def main() -> None:
    # --- Connect to MCP server ---
    mcp_client = MCPClient()
    try:
        await mcp_client.connect_stdio(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        )
        print(f"Connected via {mcp_client.transport_type} transport")

        # --- Discover tools ---
        mcp_tools = await mcp_client.discover_tools()
        print(f"Discovered {len(mcp_tools)} tools:")
        for t in mcp_tools:
            print(f"  {t.name}: {t.description}")

        tool_map = {t.name: t for t in mcp_tools}

        # --- Call LLM with tool schemas ---
        client = create_model_client(CHAT_MODEL, api_keys=API_KEYS)
        messages = [
            SystemMessage(content="You have filesystem access via MCP tools."),
            UserMessage(
                content=[
                    TextBlock(
                        text="List the files in /tmp and tell me how many there are."
                    )
                ]
            ),
        ]

        response = await client.generate(
            messages=messages,
            tools=[t.get_openai_schema() for t in mcp_tools],
        )
        print(f"\nConfigured model: {CHAT_MODEL}")
        print(f"Response: {response.content}")

        # --- Execute requested tool calls ---
        if response.tool_calls:
            messages.append(response)
            for tc in response.tool_calls:
                name = tc.name
                args = (
                    tc.arguments
                    if isinstance(tc.arguments, dict)
                    else json.loads(tc.arguments)
                )
                print(f"\nTool call: {name}({args})")
                tool = tool_map.get(name)
                if tool:
                    result = await tool.run(**args)
                    first_text = result.content[0].text if result.content else ""
                    print(f"Result: {first_text[:200]}")
                    messages.append(
                        ToolExecutionResultMessage.from_tool_result(
                            tool_result=result,
                            tool_call_id=tc.tool_call_id,
                            tool_name=name,
                        )
                    )

            # --- Send tool results back to get the final answer ---
            final = await client.generate(messages=messages, tools=[])
            print(f"\nFinal response: {final.content}")

    except Exception as e:
        print(f"MCP error (is npx installed?): {e}")
    finally:
        if mcp_client.is_connected:
            await mcp_client.disconnect()
            print("Disconnected from MCP server")


if __name__ == "__main__":
    asyncio.run(main())
