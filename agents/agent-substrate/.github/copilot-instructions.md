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

## Running the Server
```bash
# 1. Start infrastructure
docker compose up -d postgres redis

# 2. Start backend (port 8001)
uv run uvicorn agent_framework.server.app:app --port 8001 --reload

# 3. Run tests
uv run pytest

# 4. Format / lint
uv run ruff format .
uv run ruff check .
```

---

## Environment Variables (`.env` at repo root)
```
OPENAI_API_KEY=...
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb
REDIS_URL=redis://localhost:6379/0
SPOTIFY_CLIENT_ID=...        # optional
SPOTIFY_CLIENT_SECRET=...    # optional
CODE_INTERPRETER_URL=...     # optional
```

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
