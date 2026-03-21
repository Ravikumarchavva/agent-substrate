# Agent Framework — Claude Instructions

This file is read automatically by Claude when working in this repo.
Trust it as the primary reference; only search the codebase if something here is incomplete or appears incorrect.

---

## Project Summary

Python async AI-agent framework with **two deployment modes**:

1. **Monolith** — single FastAPI server at `src/agent_framework/server/`
2. **Microservices** — 11 independent FastAPI services under `src/agent_framework/services/`

Stack: Python 3.13, FastAPI, SQLAlchemy 2 async, asyncpg, PostgreSQL 16, Redis 7, OpenTelemetry → Tempo.

Package manager: **`uv`** (never `pip`).

---

## Bootstrap & Run

```bash
# Install dependencies (always first)
uv sync

# Start infrastructure
docker compose up -d postgres redis

# Optional: MCP SSE demo server (needed for examples 04/05/06)
docker compose --profile mcp up -d mcp-server   # → localhost:9000/sse

# Start monolith backend
uv run uvicorn agent_framework.server.app:app --port 8001 --reload

# Run tests
uv run pytest

# Lint / format
uv run ruff check .
uv run ruff format .
```

---

## Full Directory Map

```
src/agent_framework/
├── server/                    ← Monolith FastAPI server
│   ├── app.py                 ← Factory + lifespan; DI via app.state.*
│   ├── routes/                ← 14 route files (chat, tasks, hitl, threads, mcp_apps, …)
│   ├── models.py              ← SQLAlchemy ORM models
│   ├── database.py            ← Async session factory
│   └── schemas.py             ← Pydantic request/response models
│
├── services/                  ← Microservices (one FastAPI app per folder)
│   ├── base.py                ← Shared ServiceBase class
│   ├── admin/                 ← User management, system stats
│   ├── agent_runtime/         ← Runs the ReAct agent loop; dispatched by job_controller
│   ├── conversation/          ← Thread + message CRUD
│   ├── file_store/            ← File artifact upload/download (S3-compatible)
│   ├── gateway/               ← BFF proxy — single external ingress point
│   ├── human_gate/            ← HITL: ask_human + tool_approval flows
│   ├── identity/              ← JWT auth, OAuth, user identity
│   ├── job_controller/        ← Job lifecycle: create/start/complete/fail/cancel (JobRun ORM model)
│   ├── live_stream/           ← SSE projector — fans out events to connected clients
│   ├── policy/                ← RBAC authorization checks
│   └── tool_executor/         ← Executes individual tools in isolated contexts
│
├── core/                      ← Framework primitives
│   ├── agents/                ← BaseAgent, ReActAgent, OrchestratorAgent, FlowAgent
│   ├── memory/                ← RedisMemory, PostgresMemory, SlidingWindowMemory, SessionManager
│   ├── tools/                 ← BaseTool, ToolResult, ToolRegistry
│   ├── context/               ← RedisModelContext, build() for prompt assembly
│   ├── messages/              ← SystemMessage, UserMessage, AssistantMessage, ToolCallMessage, …
│   ├── guardrails/            ← ContentFilter, PII, PromptInjection, MaxToken, ToolCallValidation
│   ├── pipelines/             ← Codegen and sequential processing pipelines
│   ├── storage/               ← Local, S3, encrypted, tenant-aware backends
│   └── structured/            ← Structured output parsing
│
├── providers/
│   └── llm/openai/
│       └── openai_client.py   ← OpenAIClient — the only LLM provider currently wired
│
├── extensions/
│   ├── mcp/                   ← MCPClient, MCPTool wrappers, MCP App tools, app_tool_base
│   └── skills/                ← SkillManager, YAML frontmatter SKILL.md loader
│
├── shared/                    ← Cross-microservice contracts and utilities
│   ├── contracts/             ← Pydantic DTOs per service domain
│   │   ├── admin.py           ← AdminStats, UserSummary
│   │   ├── auth.py            ← TokenPayload, LoginRequest
│   │   ├── conversation.py    ← ConversationCreate, MessageCreate
│   │   ├── file_store.py      ← FileUploadResponse, FileMetadata
│   │   ├── human_gate.py      ← HITLRequest, HITLResponse
│   │   ├── job_controller.py  ← JobRunRequest, JobRunResponse
│   │   └── tool.py            ← ToolCallRequest, ToolCallResponse
│   ├── events/
│   │   ├── bus.py             ← EventBus (Redis pub/sub): connect/disconnect/publish/subscribe
│   │   └── types.py           ← Event factories: workflow_started, workflow_completed,
│   │                             workflow_failed, workflow_cancelled, agent_*, tool_*, hitl_*, …
│   ├── auth/                  ← JWT verification utils
│   └── database/              ← Shared SQLAlchemy session factory
│
├── configs/settings.py        ← Pydantic Settings (reads from .env)
├── evals/                     ← Evaluation framework (runner, judge, criteria, models)
├── mcp_apps/                  ← Pre-built MCP App HTML widgets (kanban, spotify, visualizer, …)
├── code_interpreter_service/  ← Firecracker VM sandbox (separate from services/)
└── cli.py                     ← CLI entry: `agent-framework start/stop`
```

---

## Microservices — Roles & ORM Models

| Service | Key ORM Model | Responsibility |
|---|---|---|
| `gateway` | — | BFF proxy, single external ingress |
| `identity` | `User` | JWT issuance, OAuth, user auth |
| `policy` | `Policy` | RBAC — authorize actions |
| `conversation` | `Thread`, `Message` | Thread + message persistence |
| `job_controller` | `JobRun` | Job lifecycle: dispatch → complete/fail |
| `agent_runtime` | — | Run ReAct agent loop per JobRun |
| `tool_executor` | — | Execute individual tools in isolation |
| `human_gate` | `HITLRequest` | HITL: pause job and ask human |
| `live_stream` | — | SSE projector, subscribed to EventBus |
| `file_store` | `FileRecord` | File upload/download storage |
| `admin` | `AdminLog` | Admin CRUD (users, stats) |

### Standard Service File Layout

```
services/<name>/
├── app.py       ← FastAPI factory + lifespan, wires app.state.*
├── models.py    ← SQLAlchemy ORM models (service-private DB tables)
├── routes.py    ← APIRouter with all endpoints
├── service.py   ← Business logic (called from routes, emits events)
└── __init__.py
```

Services intentionally missing `models.py`/`service.py` by design: `gateway` (BFF proxy), `live_stream` (SSE projector), `tool_executor` (executor pattern).

---

## Key Patterns

### Tool Creation — always subclass `BaseTool`

```python
from agent_framework.core.tools.base_tool import BaseTool, ToolResult

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

Register in `server/app.py` lifespan under `app.state.tools`.

### LLM Client — exact import path

```python
# ✅ Correct
from agent_framework.providers.llm.openai.openai_client import OpenAIClient

# ❌ Wrong — file does not exist
from agent_framework.providers.llm.openai.client import OpenAIClient
```

### MCP Tools — load at runtime via MCPClient

```python
from agent_framework.extensions.mcp import MCPClient

client = MCPClient(url="http://localhost:9000/sse")
tools = await client.discover_tools()   # returns list[MCPTool]
```

There is **no** `extensions.mcp.loader` module. Do not import from it.

### Shared Event Bus — always use factory functions

```python
from agent_framework.shared.events.bus import EventBus
from agent_framework.shared.events.types import workflow_started, workflow_failed

bus: EventBus = app.state.bus
await bus.publish(workflow_started(job_id=job.id, run_id=run.id))
```

Never construct event dicts manually — always use the factory functions from `shared/events/types.py`.

### SSE Event Bus (monolith only)

```python
from agent_framework.web_hitl import WebHITLBridge

bridge: WebHITLBridge = request.app.state.bridge
await bridge.put_event({"type": "my_event", "data": {...}})
```

### New Route (monolith)

1. Create `server/routes/my_feature.py` with `router = APIRouter(prefix="/my-feature")`
2. Mount in `server/app.py → create_app()` via `app.include_router(...)`

---

## Memory — `RedisMemory`

```python
from agent_framework.core.memory import RedisMemory

mem = RedisMemory(session_id="conv-abc-123", redis_url=REDIS_URL)
await mem.connect()
await mem.restore()          # reloads full history from Redis
await mem.add_message(msg)   # async — always await
msgs = await mem.get_messages()
await mem.disconnect()       # ← correct method name
```

### Memory Architecture — CRITICAL

- All `Memory` methods are `async def`. **Always `await` them.**
- **Never** add `add_message_sync()` / `add_message_async()` pairs.
- Lifecycle: `connect()` → use → `disconnect()`. **There is no `close()` method.**
- One Redis class: `RedisMemory`. Do **not** create a `RedisBackedMemory` wrapper.
- `RedisModelContext.build()` reads via `await memory.get_messages()` — ignores per-instance RAM.

---

## Message Content Types

| Type | `content` | Note |
|---|---|---|
| `SystemMessage` | `str` | Plain string |
| `UserMessage` | `list[ContentPart]` | Always a list |
| `AssistantMessage` | `Optional[list[MediaType]]` | List or `None` (tool-call-only turn) |
| `ToolExecutionResultMessage` | `str` | Plus `tool_call_id`, `name` fields |

`ToolCallMessage` fields: `tc.name`, `tc.arguments` (dict) — **not** `tc.function['name']`.

### MCPTool Schema Methods

| Method | Returns | Use when |
|---|---|---|
| `tool.get_schema()` | `Tool` (framework) | Pass to `ReActAgent(tools=[...])` |
| `tool.get_openai_schema()` | `dict` (OpenAI function-calling) | Pass to `client.generate(tools=[...])` |
| `tool.get_mcp_schema()` | `dict` (MCP wire format) | MCP protocol / debugging |

---

## Environment Variables (`.env` at repo root)

```
OPENAI_API_KEY=...
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb
REDIS_URL=redis://localhost:6379/0
REDIS_SESSION_TTL=3600
SESSION_MAX_MESSAGES=200
SESSION_AUTO_CHECKPOINT=50
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
CODE_INTERPRETER_URL=...
SYSTEM_INSTRUCTIONS=...     # per-agent system prompt override for agent_runtime service
```

**Rule:** Never add inline comments after integer values.
`python-dotenv` passes the whole string (including `# comment`) to Pydantic → `ValidationError`.
✅ `REDIS_SESSION_TTL=3600`   ❌ `REDIS_SESSION_TTL=3600  # seconds`

---

## Docker Port Mapping

| Service | Host Port | Notes |
|---|---|---|
| PostgreSQL | 5432 | `DATABASE_URL` uses `localhost:5432` |
| Redis | 6379 | `REDIS_URL` uses `localhost:6379` |
| MCP demo server | 9000 | SSE at `localhost:9000/sse` (profile: `mcp`) |
| Monolith backend | 8001 | `uv run uvicorn ... --port 8001` |
| Tempo | 4318 | OTLP HTTP |
| Grafana | 3001 | Dashboard |

Microservice ports: see `docker-compose.microservices.yml`.

---

## Coding Standards

- **Async everywhere** — every handler, service method, tool `execute()`, DB call is `async def`
- **`from __future__ import annotations`** at the top of every file
- **Type-annotate everything** — no untyped arguments or return values
- **No bare `except:`** — always catch specific exceptions
- **`app.state.*`** is the DI container — inject in lifespan, read in routes
- **`uv` only** — never `pip install` or `pip uninstall`
- **Snake_case** — files, modules, functions, variables
- Do NOT modify `main.py` (legacy dev entry point)
- New DB models → `server/models/`; new schemas → `server/schemas.py` (monolith) or service-local `models.py` (microservices)
- Built-in skills → `skills/<name>/SKILL.md` with YAML frontmatter
- MCP SSE server source → `docker/mcp_server/server.py` (FastMCP 2.x, pinned)
