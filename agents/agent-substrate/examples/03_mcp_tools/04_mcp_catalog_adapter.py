"""03-4 — MCPCatalogAdapter: register MCP tools into AgentCatalog

Shows how to bridge an MCPClient and AgentCatalog using MCPCatalogAdapter so
that MCP server tools become first-class catalog resources alongside built-in
tools. A AssistantAgent is then built from the catalog and run with the combined
tool set.

Prerequisites:
  - Node.js / npx on PATH (for the filesystem MCP server)
  - OPENAI_API_KEY (or whichever model is configured via CHAT_MODEL)
"""

import asyncio

from ravi.config import settings
from ravi.reasoning.agents.assistant import AssistantAgent
from ravi.fabric.tools.builtin_tools import CalculatorTool
from ravi.adapters.llm.factory import create_model_client
from ravi.adapters.mcp.adapter import MCPCatalogAdapter
from ravi.adapters.mcp.client import MCPClient
from ravi.kernel.agent_catalog import AgentCatalog
from ravi.fabric.memory.unbounded import UnboundedMemory

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
    # --- Setup catalog with model, memory, context, and built-in tool ---
    catalog = AgentCatalog()
    catalog.register_model(
        "primary", create_model_client(CHAT_MODEL, api_keys=API_KEYS)
    )
    catalog.register_memory("memory", UnboundedMemory())
    catalog.register_tool(CalculatorTool())

    print(f"Tools before MCP: {[t.name for t in catalog.all_tools()]}")

    # --- Connect MCP server and register tools via adapter ---
    mcp_client = MCPClient()
    try:
        await mcp_client.connect(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        )
        print(f"Connected via {mcp_client.transport_type} transport")

        adapter = MCPCatalogAdapter(catalog, namespace="mcp.filesystem")
        fqns = await adapter.register(mcp_client)
        print(f"Registered {len(fqns)} MCP tools: {fqns}")
        print(f"Tools after MCP: {[t.name for t in catalog.all_tools()]}")

        # --- Create agent using all catalog tools ---
        agent = AssistantAgent(
            name="fs-agent",
            description="Can use the calculator and filesystem MCP tools",
            catalog=catalog,
            max_iterations=5,
            verbose=True,
        )
        print(f"\nAgent has {len(agent.tools)} tools")
        print(f"Configured model: {CHAT_MODEL}")

        result = await agent.run(
            "Calculate 42 * 17, then save the result to /tmp/answer.txt"
        )
        print(f"\nResult: {result.output_text}")
        print(result.summary())

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if mcp_client.is_connected:
            await mcp_client.disconnect()
            print("Disconnected from MCP server")


if __name__ == "__main__":
    asyncio.run(main())
