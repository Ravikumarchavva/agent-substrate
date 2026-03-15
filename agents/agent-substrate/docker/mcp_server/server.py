#!/usr/bin/env python3
"""Demo MCP SSE server — exposes tools at http://0.0.0.0:9000/sse.

Start via:  docker compose --profile mcp up -d mcp-server
            python docker/mcp_server/server.py          (local dev)
"""
from __future__ import annotations

import os

from fastmcp import FastMCP

mcp = FastMCP("agent-framework-demo")

# ── Tools ──────────────────────────────────────────────────────────────────


@mcp.tool()
def add(a: float, b: float) -> float:
    """Add two numbers."""
    return a + b


@mcp.tool()
def subtract(a: float, b: float) -> float:
    """Subtract b from a."""
    return a - b


@mcp.tool()
def multiply(a: float, b: float) -> float:
    """Multiply two numbers."""
    return a * b


@mcp.tool()
def echo(message: str) -> str:
    """Echo a message back."""
    return message


@mcp.tool()
def to_uppercase(text: str) -> str:
    """Convert text to uppercase."""
    return text.upper()


@mcp.tool()
def word_count(text: str) -> dict:
    """Count words and characters in text."""
    words = text.split()
    return {"words": len(words), "characters": len(text), "sentences": text.count(".") + text.count("!") + text.count("?")}


@mcp.tool()
def server_info() -> dict:
    """Return metadata about this MCP server."""
    return {
        "name": "agent-framework-demo",
        "transport": "sse",
        "tools": ["add", "subtract", "multiply", "echo", "to_uppercase", "word_count", "server_info"],
    }


# ── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.environ.get("MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("MCP_PORT", "9000"))

    print(f"Starting MCP SSE server on http://{host}:{port}/sse")
    mcp.run(
        transport="sse",
        host=host,
        port=port,
        log_level="info",
        show_banner=False,
    )
