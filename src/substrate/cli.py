"""substrate CLI — chat with an agent in the terminal, or serve one over HTTP.

Usage:
    substrate chat                         # interactive chat with a default agent
    substrate chat --model gpt-4o-mini --no-tools
    substrate serve my_module:agent        # POST /chat (SSE), POST /agui (AG-UI), /runs/..., /health

The full platform — dev infrastructure (``up``/``down``) and the multi-tenant server (``start``/``stop``/``status``) — is
the ``substrate-cloud`` application's CLI (``apps/substrate-cloud``), not part of this package.
"""

from __future__ import annotations

import argparse
import asyncio

def cmd_chat(args: argparse.Namespace) -> None:
    """Launch an interactive CLI chat session with a ReAct agent."""
    # Late imports so the CLI stays fast for server commands
    from substrate.console import Console
    from substrate.agents import ReActAgent
    from substrate.runtime import Runtime
    from substrate.tools import Toolbox
    from substrate.integrations.llm.openai.openai_client import OpenAIClient
    from substrate.context import CompactionPipeline
    from substrate.context import ContextConfig
    from substrate.context import SlidingWindowCompaction
    from substrate.stores import Store

    # Build tools
    tools = []
    if not args.no_tools:
        # MCP tools (if --mcp supplied)
        if args.mcp:
            mcp_tools = _load_mcp_tools(args.mcp)
            tools.extend(mcp_tools)

    async def _run_chat() -> None:
        toolbox: Toolbox | None = None
        if tools:
            toolbox = Toolbox()
            for t in tools:
                toolbox.add(t)
        store = Store.at("./.substrate")
        async with Runtime(store) as rt:
            agent = ReActAgent(
                args.name,
                model=OpenAIClient(model=args.model),
                tools=toolbox,
                context=ContextConfig(
                    store.threads,
                    CompactionPipeline([SlidingWindowCompaction(max_messages=1000)]),
                ),
                max_iterations=args.max_iterations,
            )
            await rt.register(agent)
            await Console(agent, runtime=rt).interactive(stream=not args.no_stream)

    # Run the interactive REPL
    asyncio.run(_run_chat())


def _load_mcp_tools(server_urls: list[str]) -> list:
    """Connect to MCP servers and load their tools.

    Each URL is an SSE endpoint, e.g. ``http://localhost:3000/sse``.
    """
    tools: list = []
    try:
        from substrate.integrations.tools.mcp import MCPClient
    except ImportError:
        print("⚠ MCP extension not available — skipping MCP tools.")
        return tools

    for url in server_urls:
        try:
            client = MCPClient()
            asyncio.run(client.connect_sse(url))
            discovered = asyncio.run(client.list_tools())
            tools.extend(discovered)
            print(f"  Loaded {len(discovered)} tools from {url}")
        except Exception as exc:
            print(f"  ⚠ Could not connect to {url}: {exc}")
    return tools


def cmd_serve(args: argparse.Namespace) -> None:
    """``substrate serve my_module:agent`` — serve an agent (or a function that builds one) over HTTP."""
    import uvicorn

    from substrate.server import create_app, load

    target = load(args.target)
    app = target if hasattr(target, "add_api_route") else create_app(target, store=args.store, agui_path=None if args.no_agui else "/agui")
    print(f"serving {args.target} on http://{args.host}:{args.port}  (POST /chat, POST /agui, GET /health)")
    uvicorn.run(app, host=args.host, port=args.port)


# ── CLI entry point ───────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(prog="substrate", description="Chat with an agent, or serve one over HTTP")
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    p_chat = sub.add_parser("chat", help="Interactive CLI chat with an agent")
    p_chat.add_argument(
        "--model", default="gpt-4o", help="OpenAI model name (default: gpt-4o)"
    )
    p_chat.add_argument(
        "--name", default="Assistant", help="Agent display name (default: Assistant)"
    )
    p_chat.add_argument(
        "--max-iterations", type=int, default=10, help="Max ReAct steps (default: 10)"
    )
    p_chat.add_argument(
        "--no-tools", action="store_true", help="Disable built-in tools"
    )
    p_chat.add_argument(
        "--no-stream", action="store_true", help="Use non-streaming run()"
    )
    p_chat.add_argument(
        "--verbose", action="store_true", help="Show agent reasoning logs"
    )
    p_chat.add_argument(
        "--mcp", nargs="+", metavar="URL", help="MCP SSE server URLs to connect"
    )
    p_chat.set_defaults(func=cmd_chat)

    # ── serve ──────────────────────────────────────────────────────────────
    p_serve = sub.add_parser(
        "serve", help="Serve your agent over HTTP: substrate serve my_module:agent (needs the `serve` and `server` extras)"
    )
    p_serve.add_argument("target", help="module:attribute — an agent, or a function that returns one (or a FastAPI app)")
    p_serve.add_argument("--store", default="./.substrate", help="Folder of the store (default: ./.substrate)")
    p_serve.add_argument("--host", default="127.0.0.1", help="Bind host")
    p_serve.add_argument("--port", type=int, default=8000, help="Bind port")
    p_serve.add_argument("--no-agui", action="store_true", help="Do not mount the AG-UI endpoint")
    p_serve.set_defaults(func=cmd_serve)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
