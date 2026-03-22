# Agent Framework — GitHub Copilot Instructions

## Project Overview
Python async AI-agent framework built on **FastAPI** + **PostgreSQL** + **Redis**.
Agents use ReAct reasoning, SSE streaming, human-in-the-loop (HITL) approval flows,
a pluggable tool system, MCP app integrations, and a Kanban task board.

---

## Stack & Tooling
| Concern | Choice |
|---|---|
| Python runtime | **3.13** via `uv` |
| Package mgr | **uv** (never pip) — `uv add pkg`, `uv run cmd` |
| Web framework | **FastAPI** (async everywhere) |
| ORM | **SQLAlchemy 2 async** with `asyncpg` |
| Database | PostgreSQL 16 (Docker) |
| Cache / broker | Redis 7 (Docker) |
| Observability | OpenTelemetry → Tempo |
| Testing | **pytest-asyncio** — `uv run pytest` |

---

## Repository Structure
```
src/agent_framework/
├── server/
│   ├── app.py            ← FastAPI factory + lifespan (START HERE)
│   ├── routes/           ← One file per feature area
│   │   ├── chat.py       ← POST /chat SSE streaming
│   │   ├── tasks.py      ← GET/PATCH /tasks CRUD
│   │   ├── hitl.py       ← SSE + HITL approval endpoints
│   │   ├── threads.py    ← Conversation thread management
│   │   └── mcp_apps.py   ← MCP App UI registry
│   ├── database.py       ← SQLAlchemy session factory
│   └── schemas.py        ← Pydantic request/response models
├── agents/
│   ├── base_agent.py     ← BaseAgent ABC
│   └── react_agent.py    ← ReAct loop implementation
├── tools/
│   ├── base_tool.py      ← BaseTool ABC (subclass for every new tool)
│   ├── task_manager_tool.py ← Kanban board tool (SSE-driven)
│   └── mcp_app_tools.py  ← Tools that render MCP App UIs
├── tasks/
│   └── store.py          ← In-memory TaskStore singleton
├── skills/
│   ├── manager.py        ← SkillManager (XML injected into system prompt)
│   └── loader.py         ← YAML frontmatter SKILL.md loader
├── web_hitl.py           ← WebHITLBridge — SSE event bus
├── human_input.py        ← AskHumanTool
└── model_clients/
    └── openai/           ← OpenAI streaming client
```

---

## Key Architectural Patterns

### 1. Tool creation
Always subclass `BaseTool`:
```python
from agent_framework.tools.base_tool import BaseTool, ToolResult

class MyTool(BaseTool):
    def __init__(self):
        super().__init__(
            name="my_tool",
            description="What it does",
            input_schema={...}  # JSON Schema object
        )

    async def execute(self, **kwargs) -> ToolResult:
        return ToolResult(content="result", metadata={})
```
Register in `server/app.py` `lifespan` under `app.state.tools`.

### 2. SSE event bus
All real-time events to the frontend go through `WebHITLBridge`:
```python
bridge: WebHITLBridge = request.app.state.bridge
await bridge.put_event({"type": "my_event", "data": {...}})
```
`chat.py` merges bridge events with agent stream chunks into a single SSE queue.

### 3. New route
Create `server/routes/my_feature.py`:
```python
from fastapi import APIRouter
router = APIRouter(prefix="/my-feature", tags=["my-feature"])
```
Mount in `server/app.py → create_app()`:
```python
from agent_framework.server.routes.my_feature import router as my_feature_router
app.include_router(my_feature_router)
```

### 4. Task board
`TaskManagerTool` uses `contextvars.ContextVar[str]` (`current_thread_id`)
set in `chat.py` before `agent.run_stream()` so concurrent requests are isolated.
Each action fires an SSE event → `KanbanPanel` in the UI updates live.

---

## `.env` Rule
Never add inline comments after integer values. `python-dotenv` passes the whole string (including `# comment`) to Pydantic, causing `ValidationError` on settings load.
✅ `REDIS_SESSION_TTL=3600`   ❌ `REDIS_SESSION_TTL=3600  # seconds`

> For Docker ports, service commands, and full env var reference, see `CLAUDE.md`.

---

## Message Content Formats
The framework message types have different `content` constraints:

| Message type | `content` type | Example |
|---|---|---|
| `SystemMessage` | `str` | `SystemMessage(content='You are helpful.')` |
| `UserMessage` | `list[ContentPart]` | `UserMessage(content=[{'type': 'text', 'text': 'Hello'}])` |
| `AssistantMessage` | `Optional[list[MediaType]]` | `AssistantMessage(content=['Hi there'], finish_reason='stop')` |
| `ToolExecutionResultMessage` | `str` | `ToolExecutionResultMessage(content=text, tool_call_id=id, name=name)` |

> **`AssistantMessage.content` is a list, not a string.**  Pass a list of strings or
> dicts (`MediaType`).  `None` is allowed when the message only carries tool calls.

`ToolCallMessage` fields (from `response.tool_calls`): `id`, `name`, `arguments` (dict).
Access as `tc.name` and `tc.arguments` — **not** `tc.function['name']`.

## MCPTool Schema Methods
| Method | Returns | Use when |
|---|---|---|
| `tool.get_schema()` | `Tool` (internal framework object) | Pass to `ReActAgent(tools=[...])` |
| `tool.get_openai_schema()` | `dict` (OpenAI function-calling format) | Pass to `client.generate(tools=[...])` directly |
| `tool.get_mcp_schema()` | `dict` (MCP wire format) | MCP protocol / debugging |

---

## Stateless Agent Pattern (Redis-backed)
Use `RedisMemory` + `RedisModelContext` to make an agent fully stateless:
only the `session_id` is needed to restore the full conversation context.

```python
from agent_framework.core.memory import RedisMemory
from agent_framework.core.context import RedisModelContext

# Create memory with a stable session ID
mem = RedisMemory(session_id="conv-abc-123", redis_url=REDIS_URL)
await mem.connect()
await mem.restore()   # reload prior history from Redis (0 on first run)

agent = ReActAgent(
    ...
    memory=mem,
    model_context=RedisModelContext(mem, recent_n=10),
)
result = await agent.run("Hello!")

# === Stateless restore: recreate agent with SAME session_id ===
mem2 = RedisMemory(session_id="conv-abc-123", redis_url=REDIS_URL)
await mem2.connect()
await mem2.restore()              # reloads full history from Redis
# new agent continues from exactly the same point
```

Key behaviours:
- `add_message()` (**async**, called with `await` everywhere) appends to local list **and** schedules a background Redis write via `asyncio.get_running_loop().create_task()`.
- `restore()` loads the full conversation from Redis into the local list at startup.
- `RedisModelContext.build()` reads from `await redis_memory.get_messages()` — **ignores** the `raw_messages` in-process argument — so context never depends on per-instance RAM.
- Pass `session_manager=...` to enable automatic Postgres checkpointing every `auto_checkpoint_every` messages (default 50).

---

## Memory Architecture — CRITICAL RULES
**This is a v1 async-first project. Never split sync and async memory interfaces.**

- The single source of truth for memory is the `Memory` ABC in `core/memory/base_memory.py`.
  All methods are `async def`. There is **no** separate sync `BaseMemory` + async `AsyncMemory` split.
- **Never** add `add_message_sync()` / `add_message_async()` pairs or any backward-compat shims.
- **Always** `await` every memory call: `await self.memory.add_message(msg)`, `await self.memory.get_messages()`, etc.
- There is one Redis memory class: `RedisMemory`. Do **not** create a `RedisBackedMemory` wrapper.
  Use `RedisMemory(session_id=...)` for the per-session Memory ABC API.

---

## Conventions
- **Async by default** — every handler, tool, and DB call is `async def`.
- **No bare `except:`** — always catch specific exceptions.
- **Type-annotate everything** — `from __future__ import annotations` at top.
- **Snake_case** for files, modules, functions, variables.
- **`app.state.*`** is the DI container — read dependencies from it in routes.
- Do NOT modify `main.py` (legacy dev file) — all changes go into `server/`.
- New DB models go in `server/models/`, new schemas in `server/schemas.py`.
- Add built-in skills as `skills/<name>/SKILL.md` with YAML frontmatter.
- MCP SSE server source lives in `docker/mcp_server/server.py` (FastMCP 2.x, pinned).

---

## Microservices Architecture

See `services.instructions.md` for the detailed service file layout and event bus patterns.

**Non-obvious rules:**
- LLM client exact import: `from agent_framework.providers.llm.openai.openai_client import OpenAIClient` (no `client.py` alias exists — importing from the wrong path silently fails at startup).
- MCP tools: `from agent_framework.extensions.mcp import MCPClient` — there is **no** `extensions.mcp.loader` module.
- Event factory functions only — never build event dicts manually, because factory functions enforce the schema contract consumed by all subscribers.
- Services that intentionally omit `models.py`/`service.py`: `gateway` (BFF proxy), `live_stream` (SSE projector), `tool_executor` (executor pattern).
