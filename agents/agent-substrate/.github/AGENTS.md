# Agent Instructions

This file is used by Copilot coding agent and other AI agents operating in this repository.

## Required: Build & Validate

```bash
# 1. Install dependencies (always run first)
uv sync

# 2. Start infrastructure (required for integration tests)
docker compose up -d postgres redis

# 3. Run all tests
uv run pytest

# 4. Lint
uv run ruff check .

# 5. Format check (use this in CI/scripts)
uv run ruff format --check .

# 5b. Auto-fix formatting locally
uv run ruff format .
```

## Hard Rules

- **Package manager**: `uv` only. Never `pip install` or `pip uninstall`.
- **Python version**: 3.13+. Put `from __future__ import annotations` at the top of every new file.
- **Async**: All route handlers, service methods, and tool `execute()` methods must be `async def`. No sync wrappers.
- **Memory calls**: Every `Memory` method is async. Always `await` them. There are no sync alternatives.
- **Memory lifecycle**: Use `connect()` / `disconnect()`. **There is no `close()` method.**
- **No bare `except:`**: Always catch specific exception types.
- **Type annotations**: Annotate all function arguments and return types.
- **No inline env var comments**: `REDIS_SESSION_TTL=3600` ✅  `REDIS_SESSION_TTL=3600  # seconds` ❌

## Quick File Reference

| What to find | Path |
|---|---|
| Monolith FastAPI entry | `src/raavan/server/app.py` |
| Microservices | `src/raavan/services/<name>/` |
| LLM client | `src/raavan/integrations/llm/openai/openai_client.py` |
| BaseTool | `src/raavan/core/tools/base_tool.py` |
| RedisMemory | `src/raavan/core/memory/redis_memory.py` |
| Event bus | `src/raavan/shared/events/bus.py` |
| Event factories | `src/raavan/shared/events/types.py` |
| Shared contracts | `src/raavan/shared/contracts/` |
| Settings / env | `src/raavan/configs/settings.py` |

## Environment Variables

Copy `.env.example` to `.env` (or create `.env`) with at minimum:

```
OPENAI_API_KEY=...
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb
REDIS_URL=redis://localhost:6379/0
REDIS_SESSION_TTL=3600
```

## Imports to Get Right (Common Mistakes)

```python
# LLM client — file is openai_client.py, not client.py
from raavan.integrations.llm.openai.openai_client import OpenAIClient

# MCP tools — no loader module exists; use MCPClient directly
from raavan.integrations.mcp import MCPClient
tools = await MCPClient(url=...).discover_tools()

# Event bus — always use factory functions, never hand-build event dicts
from raavan.shared.events.types import workflow_started, workflow_failed
```

## Test Structure

Tests live in `tests/`. Run with `uv run pytest`. New tests should use `pytest-asyncio`.
For async tests use `@pytest.mark.asyncio` or set `asyncio_mode = "auto"` in `pytest.ini`.
