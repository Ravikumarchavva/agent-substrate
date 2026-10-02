"""A tiny MCP server over stdio, for 07_mcp_tools.py. Any MCP server works the same way."""

from mcp.server.fastmcp import FastMCP

server = FastMCP("demo")


@server.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    return a + b


@server.tool()
def shout(text: str) -> str:
    """Upper-case some text."""
    return text.upper()


if __name__ == "__main__":
    server.run("stdio")
