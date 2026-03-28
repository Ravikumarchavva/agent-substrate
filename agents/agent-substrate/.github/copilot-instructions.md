# Agent Framework — GitHub Copilot Instructions

> **Full reference**: See [`CLAUDE.md`](../CLAUDE.md) for the complete directory map,
> environment variables, Docker ports, observability stack, eval framework, and coding standards.
> This file covers only the essential patterns and critical rules.

---

## Project Overview
Python async AI-agent framework built on **FastAPI** + **PostgreSQL** + **Redis**.
Two deployment modes: **monolith** (`server/`) and **microservices** (`services/` — 12 services).
Stack: Python 3.13, `uv` (never pip), SQLAlchemy 2 async, asyncpg, OpenTelemetry.

---

## Repository Structure (top-level)
```
src/agent_framework/
├── core/            ← Framework primitives (agents, memory, tools, context, messages, guardrails)
├── integrations/    ← External adapters (LLM, audio, MCP, skills, Spotify)
├── tools/           ← Built-in tool implementations (human_input, task_manager, web_surfer, …)
├── shared/          ← Cross-service contracts, auth, events, database, observability, tasks
├── server/          ← Monolith FastAPI server (app.py, routes/, security/, services/, sse/)
├── services/        ← 12 microservices (gateway, identity, agent_runtime, conversation, …)
├── configs/         ← Pydantic Settings
└── evals/           ← LLM-as-judge evaluation framework
```

---

## Key Patterns

### Tool creation — always subclass `BaseTool`
```python
from agent_framework.core.tools.base_tool import BaseTool, ToolResult

class MyTool(BaseTool):
    def __init__(self):
        super().__init__(name="my_tool", description="...", input_schema={...})

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(content="result", metadata={})
```

### SSE event bus (monolith)
```python
bridge: WebHITLBridge = request.app.state.bridge
await bridge.put_event({"type": "my_event", "data": {...}})
```

### New route
1. Create `server/routes/my_feature.py` with `router = APIRouter(prefix="/my-feature")`
2. Mount in `server/app.py → create_app()` via `app.include_router(...)`

---

## Message Content Formats

| Message type | `content` type |
|---|---|
| `SystemMessage` | `str` |
| `UserMessage` | `list[ContentPart]` |
| `AssistantMessage` | `Optional[list[MediaType]]` — list or `None` (tool-call-only) |
| `ToolExecutionResultMessage` | `str` (+ `tool_call_id`, `name`) |

`ToolCallMessage` fields: `tc.name`, `tc.arguments` (dict) — **not** `tc.function['name']`.

---

## Memory — CRITICAL RULES

- All `Memory` methods are `async def`. **Always `await`** them.
- **Never** add sync/async pairs (`add_message_sync`, etc.).
- One Redis class: `RedisMemory`. No wrappers.
- Lifecycle: `connect()` → use → `disconnect()`. No `close()` method.

---

## `.env` Rule
Never add inline comments after integer values.
✅ `REDIS_SESSION_TTL=3600`   ❌ `REDIS_SESSION_TTL=3600  # seconds`

---

## Conventions
- **Async everywhere** — every handler, tool, DB call is `async def`.
- **`from __future__ import annotations`** at top of every file.
- **No bare `except:`** — always catch specific exceptions.
- **`app.state.*`** is the DI container.
- **`uv` only** — never pip.

---

## Non-obvious Rules
- LLM import: `from agent_framework.integrations.llm.openai.openai_client import OpenAIClient`
- MCP import: `from agent_framework.integrations.mcp import MCPClient` (no `.loader` module)
- Event factory functions only — never build event dicts manually.
- `server/security/` delegates to `shared/auth/` (thin wrapper binding settings).
- DB session: use `get_db_session` from `shared.database.dependency` — never local `_get_db`.
- Canonical enum re-exports: `from agent_framework.core import ToolRisk, RunStatus`.
- Canonical enum re-exports: `from agent_framework.core import ToolRisk, RunStatus`.
