"""loamz-agents: chat with an agent in the terminal, or run the agent server.

    loamz-agents chat                          # a ReAct agent on the configured model, history in the agent store
    loamz-agents chat --model anthropic/claude-sonnet-4-5 --mcp http://localhost:9000/sse
    loamz-agents serve --roles all             # the agent server (the `server` extra)

Configuration is the one `Settings` tree (`LOAMZ_AGENTS_*`, or a `.env`): the database, the provider keys
(`LOAMZ_AGENTS_LLM__OPENAI_API_KEY`, …) and the default model (`LOAMZ_AGENTS_LLM__MODEL`).
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any


def cmd_chat(args: argparse.Namespace) -> None:
    """An interactive chat with a ReAct agent whose history lives in the agent store."""
    from loamz.agents.bootstrap import Container, Settings
    from loamz.agents.console import Console
    from loamz.agents.kernel.agents import ReActAgent
    from loamz.agents.kernel.context import (
        CompactionPipeline,
        ContextConfig,
        SlidingWindowCompaction,
    )
    from loamz.agents.kernel.runtime import Runtime
    from loamz.agents.kernel.tools import Toolbox

    async def run() -> None:
        settings = Settings()
        container = Container(settings)
        await container.start()
        try:
            store = container.agent_store
            toolbox = Toolbox()
            for tool in await _mcp_tools(args.mcp or []):
                toolbox.add(tool)
            async with Runtime(store) as runtime:
                agent = ReActAgent(
                    args.name,
                    model=container.chat_model(args.model),
                    tools=toolbox if len(toolbox) else None,
                    context=ContextConfig(
                        store.threads,
                        CompactionPipeline([SlidingWindowCompaction(max_messages=1000)]),
                    ),
                    max_iterations=args.max_iterations,
                )
                await runtime.register(agent)
                console = Console(agent, runtime=runtime, settings=settings, tasks=store.tasks)
                await console.interactive(stream=not args.no_stream)
        finally:
            await container.aclose()

    asyncio.run(run())


async def _mcp_tools(server_urls: list[str]) -> list[Any]:
    """The tools of each MCP server (an SSE endpoint such as ``http://localhost:3000/sse``)."""
    if not server_urls:
        return []
    from loamz.agents.toolkit.mcp import MCPClient, MCPTool

    tools: list[Any] = []
    for url in server_urls:
        try:
            client = MCPClient()
            await client.connect_sse(url)
            found = await MCPTool.from_mcp_client(client)
            tools.extend(found)
            print(f"  loaded {len(found)} tools from {url}")
        except Exception as exc:  # noqa: BLE001 - a server that is down must not stop the chat
            print(f"  could not connect to {url}: {exc}")
    return tools


def cmd_serve(args: argparse.Namespace) -> None:
    """The agent server over the configured container, serving the features of ``--roles``."""
    import uvicorn

    from loamz.agents.bootstrap import Container, Settings
    from loamz.agents.server import create_app

    settings = Settings()
    roles = set(args.roles.split(",")) if args.roles else None
    app = create_app(Container(settings), roles=roles)
    uvicorn.run(app, host=args.host, port=args.port)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="loamz-agents", description="Chat with an agent, or run the agent server"
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND", required=True)

    chat = sub.add_parser("chat", help="interactive chat with an agent")
    chat.add_argument("--model", default=None, help="model (default: LOAMZ_AGENTS_LLM__MODEL)")
    chat.add_argument("--name", default="Assistant", help="agent name (default: Assistant)")
    chat.add_argument(
        "--max-iterations", type=int, default=10, help="max ReAct steps (default: 10)"
    )
    chat.add_argument(
        "--no-stream", action="store_true", help="print each answer when it is complete"
    )
    chat.add_argument(
        "--mcp", nargs="+", metavar="URL", help="MCP SSE servers whose tools to offer"
    )
    chat.set_defaults(func=cmd_chat)

    serve = sub.add_parser("serve", help="run the agent server (the `server` extra)")
    serve.add_argument(
        "--roles", default="", help="comma-separated roles (default: the settings' roles)"
    )
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
