"""03-2 — MCP Tools via SSE (HTTP) transport

Connects to a running MCP server over HTTP/SSE instead of launching a subprocess.
Use SSE when the server runs independently (e.g. in Docker), when multiple clients
share the same instance, or when the server is remote / behind authentication.

Prerequisites:
  - An MCP server listening at http://localhost:9000/sse
    Start with: docker compose -f deployment/docker/docker-compose.yml --profile mcp up -d mcp-server
  - OPENAI_API_KEY (or whichever model is configured via CHAT_MODEL)
"""

import asyncio
import json

from ravi.config import settings
from ravi.adapters.llm.factory import create_model_client
from ravi.adapters.mcp.client import MCPClient
from ravi.kernel.messages.client_messages import (
    SystemMessage,
    ToolExecutionResultMessage,
    UserMessage,
)
from ravi.kernel.messages.content import TextBlock

# Infrastructure: MCP server must be running at SSE_URL before this script runs.

SSE_URL = "http://localhost:9000/sse"

CHAT_MODEL = settings.CHAT_MODEL
API_KEYS = {
    "openai": settings.OPENAI_API_KEY,
    "anthropic": settings.ANTHROPIC_API_KEY,
    "google": settings.GEMINI_API_KEY,
    "groq": settings.GROQ_API_KEY,
    "openrouter": settings.OPENROUTER_API_KEY,
}


async def main() -> None:
    # --- Connect to MCP server via SSE ---
    mcp_client = MCPClient()
    try:
        await mcp_client.connect_sse(url=SSE_URL, headers={}, timeout=30.0)
        print(f"Connected via {mcp_client.transport_type} transport to {SSE_URL}")

        # --- Discover tools ---
        mcp_tools = await mcp_client.discover_tools()
        print(f"Discovered {len(mcp_tools)} tools:")
        for t in mcp_tools:
            print(f"  {t.name}: {t.description}")

        tool_map = {t.name: t for t in mcp_tools}

        # --- Call LLM with tool schemas ---
        client = create_model_client(CHAT_MODEL, api_keys=API_KEYS)
        messages = [
            SystemMessage(content="You are a helpful assistant with tools via MCP."),
            UserMessage(
                content=[
                    TextBlock(text='Add 42 and 58, then echo back "MCP SSE works!"')
                ]
            ),
        ]

        response = await client.generate(
            messages=messages,
            tools=[t.get_openai_schema() for t in mcp_tools],
        )

        # --- Execute requested tool calls ---
        if response.tool_calls:
            print(f"\nLLM requested {len(response.tool_calls)} tool call(s):")
            messages.append(response)
            for tc in response.tool_calls:
                name = tc.name
                args = (
                    tc.arguments
                    if isinstance(tc.arguments, dict)
                    else json.loads(tc.arguments)
                )
                print(f"  -> {name}({args})")
                tool = tool_map.get(name)
                if tool:
                    result = await tool.run(**args)
                    first_text = result.content[0].text if result.content else ""
                    print(f"  <- {first_text}")
                    messages.append(
                        ToolExecutionResultMessage.from_tool_result(
                            tool_result=result,
                            tool_call_id=tc.tool_call_id,
                            tool_name=name,
                        )
                    )

            # --- Send tool results back for the final answer ---
            final = await client.generate(messages=messages, tools=[])
            print(f"\nConfigured model: {CHAT_MODEL}")
            print(f"Final response: {final.content}")
        else:
            print(f"\nDirect response: {response.content}")

    except (RuntimeError, OSError, ConnectionRefusedError) as e:
        print(f"Connection error: {e}")
        print(
            "  Start the MCP server: "
            "docker compose -f deployment/docker/docker-compose.yml --profile mcp up -d mcp-server"
        )
    except Exception as e:
        import traceback

        traceback.print_exc()
        print(f"Error: {type(e).__name__}: {e}")
    finally:
        if mcp_client.is_connected:
            await mcp_client.disconnect()
            print("Disconnected from MCP server")


if __name__ == "__main__":
    asyncio.run(main())
