"""Tools from an MCP server.

Any Model Context Protocol server becomes a set of tools: connect, discover, hand them to the agent. Because the
engine knows nothing about what a remote tool does, each one is ``risk=HIGH`` and not idempotent unless *you* — the
operator connecting it — say otherwise. Here the demo server is ours, so we vouch for it.

    uv run python examples/07_mcp_tools.py

(Over the network it is ``await client.connect_sse(url=...)`` instead of ``connect_stdio``.)
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from _model import ToolCall, pick_model

from substrate.agents import ReActAgent
from substrate.integrations.tools.mcp import MCPClient, MCPTool
from substrate.runtime import Runtime
from substrate.tools import ToolRisk

SERVER = Path(__file__).with_name("_mcp_demo_server.py")


async def main() -> None:
    client = MCPClient()
    await client.connect_stdio(command=sys.executable, args=[str(SERVER)])
    try:
        tools = await MCPTool.from_mcp_client(client, risk=ToolRisk.SAFE, idempotent=True)
        print("discovered:", [tool.name for tool in tools])

        agent = ReActAgent(
            "calculator",
            model=pick_model(ToolCall("add", {"a": 19, "b": 23}), "19 + 23 = 42."),
            tools=tools,
        )
        async with Runtime.open("./.substrate") as runtime:
            outcome = await runtime.run(agent, "What is 19 + 23?")
            print(outcome.output)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
