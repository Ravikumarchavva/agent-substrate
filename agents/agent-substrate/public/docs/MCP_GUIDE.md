# MCP Tools Guide

This guide explains how to use MCP (Model Context Protocol) tools with the Agent Framework.

## What is MCP?

**MCP (Model Context Protocol)** is an open protocol created by Anthropic that enables AI systems to connect to external tools and data sources. It provides a standardized way for:

- **Tools**: Functions that agents can call (e.g., file operations, database queries)
- **Resources**: Data sources that agents can read (e.g., files, API endpoints)
- **Prompts**: Reusable prompt templates

## Why Use MCP?

- **🌐 Ecosystem**: Growing library of pre-built MCP servers (filesystem, databases, APIs)
- **🔌 Standardization**: Industry-standard protocol supported by major AI platforms
- **🔄 Interoperability**: Use any MCP-compliant server with your agents
- **🎯 Separation**: Tools run as separate processes for better isolation
- **🚀 Future-Proof**: Adopted by Anthropic, Google, and other major players

## Installation

MCP support is included by default. Just install the framework:

```bash
uv sync
# or
pip install -e .
```

Dependencies (`mcp` and `httpx`) are automatically installed.

## Quick Start

### 1. Basic MCP Tool Usage

```python
import asyncio
from agent_framework.tools import MCPClient, MCPTool
from agent_framework.model_clients.openai_client import OpenAIClient
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.messages.agent_messages import UserMessage

async def main():
    # Connect to MCP server (filesystem example)
    mcp_client = MCPClient()
    await mcp_client.connect(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    )
    
    # Auto-discover all tools from the server
    tools = await MCPTool.from_mcp_client(mcp_client)
    
    print(f"Discovered {len(tools)} tools:")
    for tool in tools:
        print(f"  - {tool.name}: {tool.description}")
    
    # Use tools with your agent
    client = OpenAIClient(model="gpt-4o")
    memory = UnboundedMemory()
    memory.add_message(UserMessage(content="List files in /tmp"))
    
    # Agent will automatically use MCP tools
    response = await client.generate(
        messages=memory.get_messages(),
        tools=[t.get_schema() for t in tools]
    )
    
    print(f"Response: {response.content}")
    
    # Cleanup
    await mcp_client.disconnect()

asyncio.run(main())
```

### 2. Using Specific MCP Tools

```python
import asyncio
from agent_framework.tools import MCPClient, MCPTool

async def main():
    # Connect to server
    mcp_client = MCPClient()
    await mcp_client.connect(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    )
    
    # Get all available tools
    tools_list = await mcp_client.list_tools()
    
    # Create specific tools
    read_file_tool = MCPTool(
        client=mcp_client,
        name="read_file",
        description="Read contents of a file",
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"}
            },
            "required": ["path"]
        }
    )
    
    # Execute tool directly
    result = await read_file_tool.execute(path="/tmp/test.txt")
    print(f"File contents: {result}")
    
    await mcp_client.disconnect()

asyncio.run(main())
```

### 3. Context Manager Pattern

```python
import asyncio
from agent_framework.tools import MCPClient, MCPTool

async def main():
    # Use context manager for automatic cleanup
    async with MCPClient() as mcp_client:
        await mcp_client.connect(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        )
        
        tools = await MCPTool.from_mcp_client(mcp_client)
        
        # Use tools...
        # Client automatically disconnects when exiting context

asyncio.run(main())
```

### 4. Using SSE Transport (HTTP-based)

MCP supports two transport types: **stdio** (process-based) and **SSE** (HTTP-based).

#### Stdio Transport (Default)

Launches MCP server as a subprocess:

```python
import asyncio
from agent_framework.tools import MCPClient, MCPTool

async def main():
    mcp_client = MCPClient()
    
    # Connect via stdio (launches subprocess)
    await mcp_client.connect_stdio(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    )
    
    print(f"Transport type: {mcp_client.transport_type}")  # "stdio"
    
    tools = await MCPTool.from_mcp_client(mcp_client)
    # Use tools...
    
    await mcp_client.disconnect()

asyncio.run(main())
```

#### SSE Transport (HTTP)

Connects to an already-running MCP server via HTTP:

```python
import asyncio
from agent_framework.tools import MCPClient, MCPTool

async def main():
    mcp_client = MCPClient()
    
    # Connect via SSE (HTTP endpoint)
    await mcp_client.connect_sse(
        url="http://localhost:8000/sse",
        headers={"Authorization": "Bearer your-token"},  # Optional
        timeout=30.0  # Optional timeout in seconds
    )
    
    print(f"Transport type: {mcp_client.transport_type}")  # "sse"
    
    tools = await MCPTool.from_mcp_client(mcp_client)
    # Use tools...
    
    await mcp_client.disconnect()

asyncio.run(main())
```

#### When to Use Each Transport

**Use stdio when:**
- Running MCP servers locally
- You want automatic server lifecycle management
- Working with CLI-based MCP servers (npx, python scripts)

**Use SSE when:**
- Connecting to remote MCP servers
- Server is already running (e.g., in production)
- Need to share one MCP server across multiple clients
- Working with web-based MCP services

#### Backward Compatibility

The `connect()` method defaults to stdio for backward compatibility:

```python
# These are equivalent:
await mcp_client.connect(command="npx", args=[...])
await mcp_client.connect_stdio(command="npx", args=[...])
```

## Available MCP Servers

Here are some popular MCP servers you can use:

### Filesystem Server

Access local files and directories:

```python
await mcp_client.connect(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-filesystem", "/path/to/directory"]
)
```

**Tools**: `read_file`, `write_file`, `list_directory`, `create_directory`, etc.

### GitHub Server

Interact with GitHub repositories:

```python
await mcp_client.connect(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-github"],
    env={"GITHUB_TOKEN": "your-token"}
)
```

**Tools**: `create_issue`, `search_repositories`, `get_file_contents`, etc.

### PostgreSQL Server

Query PostgreSQL databases:

```python
await mcp_client.connect(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-postgres"],
    env={"DATABASE_URL": "postgresql://..."}
)
```

**Tools**: `query`, `list_tables`, `describe_table`, etc.

### Google Drive Server

Access Google Drive files:

```python
await mcp_client.connect(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-gdrive"],
    env={"GOOGLE_APPLICATION_CREDENTIALS": "/path/to/credentials.json"}
)
```

**Tools**: `search_files`, `read_file`, `create_file`, etc.

### Brave Search Server

Web search via Brave:

```python
await mcp_client.connect(
    command="npx",
    args=["-y", "@modelcontextprotocol/server-brave-search"],
    env={"BRAVE_API_KEY": "your-key"}
)
```

**Tools**: `brave_web_search`, `brave_local_search`, etc.

## Combining Built-in and MCP Tools

You can mix built-in tools with MCP tools:

```python
import asyncio
from agent_framework.tools import MCPClient, MCPTool, CalculatorTool
from agent_framework.model_clients.openai_client import OpenAIClient
from agent_framework.memory.unbounded_memory import UnboundedMemory
from agent_framework.messages.agent_messages import UserMessage

async def main():
    # Built-in tools
    builtin_tools = [CalculatorTool()]
    
    # MCP tools
    mcp_client = MCPClient()
    await mcp_client.connect(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    )
    mcp_tools = await MCPTool.from_mcp_client(mcp_client)
    
    # Combine both
    all_tools = builtin_tools + mcp_tools
    
    # Use with agent
    client = OpenAIClient(model="gpt-4o")
    memory = UnboundedMemory()
    memory.add_message(UserMessage(
        content="Calculate 2+2 and save the result to /tmp/result.txt"
    ))
    
    response = await client.generate(
        messages=memory.get_messages(),
        tools=[t.get_schema() for t in all_tools]
    )
    
    print(response.content)
    
    await mcp_client.disconnect()

asyncio.run(main())
```

## Advanced Usage

### Multiple MCP Servers

Connect to multiple MCP servers simultaneously:

```python
import asyncio
from agent_framework.tools import MCPClient, MCPTool

async def main():
    # Filesystem server
    fs_client = MCPClient()
    await fs_client.connect(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    )
    fs_tools = await MCPTool.from_mcp_client(fs_client)
    
    # GitHub server
    gh_client = MCPClient()
    await gh_client.connect(
        command="npx",
        args=["-y", "@modelcontextprotocol/server-github"],
        env={"GITHUB_TOKEN": "your-token"}
    )
    gh_tools = await MCPTool.from_mcp_client(gh_client)
    
    # Combine tools from both servers
    all_tools = fs_tools + gh_tools
    
    # Use with agent...
    
    # Cleanup
    await fs_client.disconnect()
    await gh_client.disconnect()

asyncio.run(main())
```

### Custom MCP Server

You can create your own MCP server in Python:

```python
# my_mcp_server.py
from mcp.server import Server
from mcp.server.stdio import stdio_server

app = Server("my-custom-server")

@app.list_tools()
async def list_tools():
    return [
        {
            "name": "my_tool",
            "description": "My custom tool",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "input": {"type": "string"}
                },
                "required": ["input"]
            }
        }
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict):
    if name == "my_tool":
        return {"content": [{"type": "text", "text": f"Processed: {arguments['input']}"}]}

if __name__ == "__main__":
    stdio_server(app)
```

Connect to it:

```python
await mcp_client.connect(
    command="python",
    args=["my_mcp_server.py"]
)
```

## Error Handling

Always handle connection and execution errors:

```python
import asyncio
from agent_framework.tools import MCPClient, MCPTool

async def main():
    mcp_client = MCPClient()
    
    try:
        await mcp_client.connect(
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        )
        
        tools = await MCPTool.from_mcp_client(mcp_client)
        
        # Use tools...
        
    except RuntimeError as e:
        print(f"MCP Error: {e}")
    finally:
        if mcp_client.is_connected:
            await mcp_client.disconnect()

asyncio.run(main())
```

## Best Practices

1. **Always disconnect**: Use context managers or try/finally blocks
2. **Check connection**: Verify `mcp_client.is_connected` before using tools
3. **Handle errors**: MCP servers can fail; handle exceptions gracefully
4. **Limit scope**: Only expose necessary directories/resources to MCP servers
5. **Use environment variables**: Store API keys and credentials securely
6. **Auto-discovery**: Use `MCPTool.from_mcp_client()` for convenience
7. **Combine tools**: Mix built-in and MCP tools for maximum flexibility

## Troubleshooting

### "npx command not found"

Install Node.js:
```bash
# Windows (using winget)
winget install OpenJS.NodeJS

# macOS
brew install node

# Linux
sudo apt install nodejs npm
```

### "MCP client not connected"

Ensure you call `await mcp_client.connect()` before using tools:
```python
await mcp_client.connect(command="npx", args=[...])
tools = await MCPTool.from_mcp_client(mcp_client)  # Now works
```

### "Server failed to start"

Check that the MCP server package is available:
```bash
npx -y @modelcontextprotocol/server-filesystem --help
```

## Resources

- **MCP Documentation**: https://modelcontextprotocol.io
- **MCP Servers**: https://github.com/modelcontextprotocol/servers
- **MCP Specification**: https://spec.modelcontextprotocol.io
- **Community Servers**: https://github.com/topics/mcp-server

---

Happy building with MCP! 🚀
