"""raavan CLI

Usage:
    raavan start          # start server on default port 8001
    raavan start --port 9000 --reload
    raavan stop           # stop a running server (via PID file)
    raavan status         # check if server is running
    raavan chat           # interactive CLI chat with default agent
    raavan chat --model gpt-4o-mini --no-tools
"""

from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path

_PID_DIR = Path.home() / ".raavan"
_PID_FILE = _PID_DIR / "server.pid"


# ── helpers ──────────────────────────────────────────────────────────────────


def _read_pid() -> int | None:
    try:
        return int(_PID_FILE.read_text().strip())
    except (FileNotFoundError, ValueError):
        return None


def _write_pid(pid: int) -> None:
    _PID_DIR.mkdir(parents=True, exist_ok=True)
    _PID_FILE.write_text(str(pid))


def _remove_pid() -> None:
    try:
        _PID_FILE.unlink()
    except FileNotFoundError:
        pass


def _is_running(pid: int) -> bool:
    """Return True if a process with *pid* is alive."""
    try:
        # signal 0 just checks existence; works on Windows & POSIX.
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# ── commands ─────────────────────────────────────────────────────────────────


def cmd_start(args: argparse.Namespace) -> None:
    """Start the uvicorn server and write its PID to the PID file."""
    pid = _read_pid()
    if pid and _is_running(pid):
        print(f"Agent Framework is already running (PID {pid}).")
        print("  Run `raavan stop` to stop it first.")
        sys.exit(1)

    host = args.host
    port = args.port
    reload = args.reload
    workers = args.workers

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "raavan.server.app:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if reload:
        cmd.append("--reload")
    elif workers > 1:
        cmd += ["--workers", str(workers)]

    if args.foreground or reload:
        # Run in the foreground (Ctrl+C to stop); no PID file needed.
        print(f"Starting Agent Framework on http://{host}:{port} (foreground)…")
        try:
            subprocess.run(cmd, check=True)
        except KeyboardInterrupt:
            pass
        return

    # Background mode — detach the process.
    print(f"Starting Agent Framework on http://{host}:{port} (background)…")
    kwargs: dict = {}
    if sys.platform == "win32":
        # Windows: use DETACHED_PROCESS + CREATE_NEW_PROCESS_GROUP
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        kwargs["creationflags"] = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
        kwargs["close_fds"] = True
    else:
        kwargs["start_new_session"] = True

    log_file = _PID_DIR / "server.log"
    _PID_DIR.mkdir(parents=True, exist_ok=True)
    log_fp = open(log_file, "a")  # noqa: SIM115  (intentionally kept open by child)

    proc = subprocess.Popen(
        cmd,
        stdout=log_fp,
        stderr=log_fp,
        **kwargs,
    )
    _write_pid(proc.pid)
    print(f"  PID        : {proc.pid}")
    print(f"  Log file   : {log_file}")
    print("  Stop with  : raavan stop")


def cmd_stop(args: argparse.Namespace) -> None:
    """Send SIGTERM (or SIGKILL with --force) to the server process."""
    pid = _read_pid()
    if pid is None:
        print("No PID file found — is the server running?")
        sys.exit(1)

    if not _is_running(pid):
        print(f"Process {pid} is not running. Cleaning up stale PID file.")
        _remove_pid()
        return

    sig = (
        getattr(signal, "SIGKILL", signal.SIGTERM)
        if (hasattr(args, "force") and args.force)
        else signal.SIGTERM
    )
    try:
        os.kill(pid, sig)
    except PermissionError:
        print(f"Permission denied to kill PID {pid}.")
        sys.exit(1)

    # Wait up to 5 s for clean exit.
    import time

    for _ in range(50):
        time.sleep(0.1)
        if not _is_running(pid):
            _remove_pid()
            print(f"Agent Framework (PID {pid}) stopped.")
            return

    print(f"Process {pid} did not exit. Use `raavan stop --force` to kill it.")
    sys.exit(1)


def cmd_status(args: argparse.Namespace) -> None:  # noqa: ARG001
    """Print running / stopped status."""
    pid = _read_pid()
    if pid is None:
        print("Agent Framework: STOPPED (no PID file)")
        return
    if _is_running(pid):
        print(f"Agent Framework: RUNNING  (PID {pid})")
    else:
        print(f"Agent Framework: STOPPED  (stale PID {pid})")
        _remove_pid()


def cmd_chat(args: argparse.Namespace) -> None:
    """Launch an interactive CLI chat session with a ReAct agent."""
    # Late imports so the CLI stays fast for server commands
    from raavan.core.console import Console
    from raavan.core.agents.react_agent import ReActAgent
    from raavan.integrations.llm.openai.openai_client import OpenAIClient
    from raavan.core.memory.unbounded_memory import UnboundedMemory
    from raavan.core.context.implementations import UnboundedContext

    # Build tools
    tools = []
    if not args.no_tools:
        from raavan.core.tools.builtin_tools import (
            CalculatorTool,
            GetCurrentTimeTool,
        )

        tools = [CalculatorTool(), GetCurrentTimeTool()]

        # MCP tools (if --mcp supplied)
        if args.mcp:
            mcp_tools = _load_mcp_tools(args.mcp)
            tools.extend(mcp_tools)

    client = OpenAIClient(model=args.model)
    agent = ReActAgent(
        name=args.name,
        description="Interactive CLI assistant",
        model_client=client,
        tools=tools,
        memory=UnboundedMemory(),
        model_context=UnboundedContext(),
        max_iterations=args.max_iterations,
        verbose=args.verbose,
    )

    # Run the interactive REPL
    asyncio.run(Console(agent).interactive(stream=not args.no_stream))


def _load_mcp_tools(server_urls: list[str]) -> list:
    """Connect to MCP servers and load their tools.

    Each URL is an SSE endpoint, e.g. ``http://localhost:3000/sse``.
    """
    tools: list = []
    try:
        from raavan.integrations.mcp import MCPClient
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


# ── CLI entry point ───────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="raavan",
        description="Agent Framework server manager",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # ── start ──────────────────────────────────────────────────────────────
    p_start = sub.add_parser("start", help="Start the server")
    p_start.add_argument(
        "--host", default="127.0.0.1", help="Bind host  (default: 127.0.0.1)"
    )
    p_start.add_argument(
        "--port", "-p", default=8001, type=int, help="Bind port  (default: 8001)"
    )
    p_start.add_argument(
        "--reload",
        action="store_true",
        help="Enable auto-reload (dev mode, runs foreground)",
    )
    p_start.add_argument(
        "--workers", default=1, type=int, help="Number of uvicorn workers (default: 1)"
    )
    p_start.add_argument(
        "--foreground",
        action="store_true",
        help="Run in foreground instead of background",
    )
    p_start.set_defaults(func=cmd_start)

    # ── stop ───────────────────────────────────────────────────────────────
    p_stop = sub.add_parser("stop", help="Stop the running server")
    p_stop.add_argument(
        "--force", action="store_true", help="SIGKILL instead of SIGTERM"
    )
    p_stop.set_defaults(func=cmd_stop)

    # ── status ─────────────────────────────────────────────────────────────
    p_status = sub.add_parser("status", help="Show server status")
    p_status.set_defaults(func=cmd_status)

    # ── chat ───────────────────────────────────────────────────────────────
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

    # ── restart ────────────────────────────────────────────────────────────
    p_restart = sub.add_parser("restart", help="Stop then start the server")
    p_restart.add_argument("--host", default="127.0.0.1", help="Bind host")
    p_restart.add_argument("--port", "-p", default=8001, type=int, help="Bind port")
    p_restart.add_argument("--reload", action="store_true")
    p_restart.add_argument("--workers", default=1, type=int)
    p_restart.add_argument("--foreground", action="store_true")
    p_restart.add_argument("--force", action="store_true")

    def cmd_restart(a: argparse.Namespace) -> None:
        cmd_stop(a)
        cmd_start(a)

    p_restart.set_defaults(func=cmd_restart)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
