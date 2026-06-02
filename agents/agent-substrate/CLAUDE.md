# Agent Framework — Claude Instructions

This file is read automatically by Claude when working in this repo.
Trust it as the primary reference; only search the codebase if something here is incomplete or appears incorrect.

---

## Project Summary

Python async AI-agent framework with two deployment modes:

1. **Monolith** — single FastAPI server at `src/ravi/serving/monolith/`
2. **Microservices** — 12 independent FastAPI services at `src/ravi/serving/services/`

Stack: Python 3.13, FastAPI, SQLAlchemy 2 async, asyncpg, PostgreSQL 18, Redis 7, OpenTelemetry → Tempo.

Package manager: **`uv`** (never `pip`).

---

## Bootstrap & Run

```bash
# Install dependencies (always first)
uv sync

# Start infrastructure (Postgres, Redis, MinIO, Restate, NATS, observability, MCP server)
make infra-up

# Start monolith backend (port 8001)
uv run start

# With hot-reload
uv run start --reload

# Run tests
uv run pytest

# Lint / format
uv run python -m ruff check .
uv run python -m ruff format .
```

---

## Full Directory Map

```
ravi-engine/                     ← repo root
├── src/ravi/                    ← Python package (all application code)
├── deployment/                  ← All deployment artefacts
│   ├── docker/                  ← Dockerfiles + Compose files
│   │   ├── backend.Dockerfile
│   │   ├── docker-compose.yml   ← Local dev (monolith + infra)
│   │   ├── docker-compose.microservices.yml
│   │   └── mcp_server/          ← FastMCP 2.x demo SSE server
│   └── k8s/                     ← Kubernetes / Kustomize manifests
├── docs/                        ← Architecture docs, design patterns, archive
├── examples/                    ← Jupyter notebooks
├── tests/                       ← pytest suite
├── legacy/                      ← Archived pre-migration code (do not import)
├── Makefile                     ← Common dev targets
└── pyproject.toml               ← uv project + ruff + import-linter config
```

```
src/ravi/
├── kernel/       L0 — FROZEN. Pure contracts: types, Protocols, dataclasses. No I/O.
│   ├── content.py        ContentBlock, TextBlock, ChatMessage, ToolUseBlock, …
│   ├── message.py        Message, MessageContext, MessageHandler, Subscription
│   ├── tools.py          Tool Protocol, ToolExecutionResult, ToolRisk, ToolCallRequest
│   ├── llm.py            LLMClient, EmbeddingClient Protocols
│   ├── history.py        HistoryProvider Protocol
│   ├── context.py        CompactionStrategy, AgentContextProtocol
│   ├── middleware.py     Interceptor Protocol
│   ├── stream.py         TextDelta, ReasoningDelta, CompletionEvent, StreamDone
│   ├── protocol.py       AgentRuntime Protocol
│   ├── identity.py       AgentId, TopicId
│   └── errors.py
│
├── agents/       L1-L3 combined — context, LLM clients, guardrails, middleware, agent types
│   ├── assistant/        AssistantAgent (ReAct loop, tools, HITL, guardrail injection)
│   ├── context/          AgentContext, InMemoryHistoryProvider, compaction strategies
│   ├── llm/              LLMClient (concrete), EmbeddingClient, model registry, cache/fallback/router
│   ├── guardrails/       GuardrailType, run_guardrails, ContentFilter, LLMJudge, MaxToken, PII, PromptInjection
│   ├── middleware/       Interceptor, MiddlewarePipeline, AuditLogger
│   ├── flow/             FlowAgent (multi-step graph execution)
│   ├── orchestrator/     OrchestratorAgent
│   ├── proxy/            UserProxyAgent
│   ├── runtime/          LocalRuntime (message dispatch)
│   ├── hooks/            lifecycle hooks
│   ├── resources/        ExecutionBudget, agent_span
│   └── supervision/      RetryPolicy
│
├── adapters/     concrete I/O ports implementing kernel/agents contracts
│   ├── llm/              openai/, anthropic/, gemini/, encoders/, factory.py
│   ├── memory/           redis_history.py, postgres_history.py
│   ├── mcp/              MCPClient, MCPTool, apps/
│   ├── vector/           vector store adapters
│   ├── graph/            graph store adapters
│   ├── storage/          file storage adapters
│   ├── events/           EventBus (Redis pub/sub)
│   └── spotify/          Spotify API adapter
│
├── capabilities/ the agent's runtime capabilities
│   ├── tools/            tool implementations (human_input, task_manager, web_surfer, …)
│   ├── skills/           SKILL.md prompt-skill packages
│   ├── knowledge/        RAG pipeline, loaders, graph_rag
│   ├── connectors/       external service connectors (email, calendar, minio, postgres)
│   ├── triggers/         event-based trigger definitions
│   └── internal/         scanner, pipeline engine, skill loader, chain runtime
│
├── serving/      deployment shells
│   ├── monolith/         single FastAPI app (app.py, routes/, sse/, security/, services/)
│   ├── services/         12 independent microservices (one FastAPI app per folder)
│   └── shared/           cross-service: auth, database, events, observability, tasks
│
├── evals/        LLM-as-judge eval framework (judge, criteria, runner, models)
├── config.py     Pydantic Settings (reads .env)
├── exceptions.py public exceptions (GuardrailTripwireError, …)
├── logger.py     setup_logging()
├── cli.py        CLI entry point
└── console.py    interactive console
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
| `agent_runtime` | — | Run agent loop per JobRun |
| `tool_executor` | — | Execute individual tools in isolation |
| `human_gate` | `HITLRequest` | HITL: pause job and ask human |
| `live_stream` | — | SSE projector, subscribed to EventBus |
| `file_store` | `FileRecord` | File upload/download storage |
| `admin` | `AdminLog` | Admin CRUD (users, stats) |
| `code_interpreter` | — | Firecracker VM sandbox for code execution |

### Standard Service File Layout

```
serving/services/<name>/
├── app.py       ← FastAPI factory + lifespan, wires app.state.*
├── models.py    ← SQLAlchemy ORM models (service-private DB tables)
├── routes.py    ← APIRouter with all endpoints
├── service.py   ← Business logic (called from routes, emits events)
└── __init__.py
```

Services intentionally missing `models.py`/`service.py` by design: `gateway` (BFF proxy), `live_stream` (SSE projector), `tool_executor` (executor pattern).

---

## Frozen kernel — `ravi.kernel` is frozen forever

`src/ravi/kernel/` is the contract layer. It is **never edited to add capability**. New features go in `agents/`, `adapters/`, or `capabilities/`.

**Dependency rule** (strictly downward; enforced by `uv run lint-imports`):

```
agents  →  kernel        (L1-L3 imports down into L0)

adapters, capabilities, evals, serving  =  orthogonal
```

**Enforcement** (CI fails if violated):

1. `tool.importlinter` contract `kernel is independent` — `ravi.kernel` may not import from any other ravi package.
2. `tests/architecture/test_kernel_invariants.py` — LOC ceiling, file-count ceiling, no concrete implementations in kernel.

---

## Key Patterns

### Adding capability

| You want to add… | Write it in… |
|---|---|
| A new agent type | `agents/<name>/agent.py` — follow `AssistantAgent` pattern |
| A new guardrail | `agents/guardrails/<name>.py` — implement `run(ctx) -> GuardrailResult` |
| A new LLM provider | `adapters/llm/<provider>/` — implement `LLMClient` Protocol from `kernel/llm.py` |
| A new memory backend | `adapters/memory/<name>.py` — implement `HistoryProvider` Protocol from `kernel/history.py` |
| A new tool | `capabilities/tools/<name>/tool.py` — implement `Tool` Protocol (auto-scanned, no registration needed) |
| A new skill | `capabilities/skills/<name>/SKILL.md` — YAML frontmatter + prompt body |

### Tool creation

```python
from ravi.kernel.tools import ToolExecutionResult
from ravi.kernel.content import TextBlock

class MyTool:
    name = "my_tool"
    description = "What it does"
    input_schema = {"type": "object", "properties": {...}, "required": [...]}

    async def execute(self, *, param: str) -> ToolExecutionResult:  # type: ignore[override]
        return ToolExecutionResult(content=[TextBlock(text="result")])
```

Placed at `capabilities/tools/my_tool/tool.py` — `CatalogScanner` discovers it automatically.

### LLM client

```python
from ravi.adapters.llm.factory import create_model_client
client = create_model_client("gpt-4o", api_keys={"openai": "..."})
```

### MCP tools

```python
from ravi.adapters.mcp import MCPClient

client = MCPClient(url="http://localhost:9000/sse")
tools = await client.discover_tools()   # returns list[MCPTool]
```

### Event bus — always use factory functions

```python
from ravi.serving.shared.events.bus import EventBus
from ravi.serving.shared.events.types import workflow_started

bus: EventBus = app.state.bus
await bus.publish(workflow_started(job_id=job.id, run_id=run.id))
```

Never construct event dicts manually — always use the factory functions from `serving/shared/events/types.py`.

### SSE event bus (monolith only)

```python
from ravi.serving.monolith.sse.bridge import WebHITLBridge

bridge: WebHITLBridge = request.app.state.bridge
await bridge.put_event({"type": "my_event", "data": {...}})
```

### New monolith route

1. Create `serving/monolith/routes/my_feature.py` with `router = APIRouter(prefix="/my-feature")`
2. Mount in `serving/monolith/app.py → create_app()` via `app.include_router(...)`

### DI pattern — `app.state.*`

All shared objects (LLM clients, tool registry, event bus, HITL bridge) are wired in lifespan and accessed via `request.app.state.*`. No global singletons.

---

## Memory / History

```python
# In-memory (default, for testing)
from ravi.agents.context import InMemoryHistoryProvider

# Redis-backed
from ravi.adapters.memory.redis_history import RedisHistoryProvider

# Postgres-backed
from ravi.adapters.memory.postgres_history import PostgresHistoryProvider
```

All `HistoryProvider` methods are `async def`. Always `await` them.

---

## Environment Variables (`.env` at repo root)

```
# LLM providers (set at least one)
OPENAI_API_KEY=...
ANTHROPIC_API_KEY=...
GEMINI_API_KEY=...

# Database (async for ORM, sync for Alembic)
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb
ASYNC_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb

# Redis
REDIS_URL=redis://localhost:6379/0
REDIS_SESSION_TTL=3600

# Session
SESSION_MAX_MESSAGES=200
SESSION_AUTO_CHECKPOINT=50

# Models (override globally or let per-request override take precedence)
CHAT_MODEL=openai/gpt-5.4-mini
EMBEDDING_MODEL=text-embedding-3-small

# Spotify (optional — only if using SpotifyPlayerTool)
SPOTIFY_CLIENT_ID=...
SPOTIFY_CLIENT_SECRET=...
SPOTIFY_REDIRECT_URI=http://localhost:8001/auth/spotify/callback

# CORS (comma-separated origins)
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3002

# Observability
OTLP_ENDPOINT=http://localhost:4318

# Auth
JWT_SECRET=<32+ char random string — required>

# Code interpreter (optional)
CODE_INTERPRETER_URL=...
```

**Rule:** Never add inline comments after integer values.
`python-dotenv` passes the full string to Pydantic → `ValidationError`.
✅ `REDIS_SESSION_TTL=3600`   ❌ `REDIS_SESSION_TTL=3600  # seconds`

---

## Docker Port Mapping

| Service | Host Port | Notes |
|---|---|---|
| PostgreSQL | 5432 | `DATABASE_URL` uses `localhost:5432` |
| Redis | 6379 | `REDIS_URL` uses `localhost:6379` |
| MCP demo server | 9000 | SSE at `localhost:9000/sse` |
| Monolith backend | 8001 | `uv run start` |
| Tempo | 4318 | OTLP HTTP |
| Grafana | 3001 | Dashboard |

Microservice ports: see `deployment/docker/docker-compose.microservices.yml`.

---

## Observability Stack

All observability services start via `make infra-up`.

| Component | Purpose |
|---|---|
| Loki + Promtail | Log aggregation — scrapes pod/service stdout |
| Tempo | Distributed tracing (OTLP → `localhost:4318`) |
| Prometheus | Metrics collection |
| Grafana | Dashboards at `http://localhost:3001` (admin/admin) |

Logging convention in Python modules:
```python
from ravi.logger import setup_logging
logger = setup_logging("ravi.my_module")
```
Do not call `logging.getLogger(...)` directly.

---

## CI/CD

GitHub Actions workflows in `.github/workflows/`:
- **Lint**: Ruff check + format
- **Type check**: Pyright (soft-fail)
- **Test**: pytest with Postgres + Redis services
- **Build**: Docker image to GHCR
- **Security**: pip-audit

Local preflight (mirrors CI):
```bash
make ci
```

---

## Evaluation Framework (`evals/`)

```python
from ravi.evals import EvalCase, EvalDataset, LLMJudge, EvalRunner, CORRECTNESS

runner = EvalRunner(agent=my_agent, judge=LLMJudge(criteria=[CORRECTNESS]))
report = await runner.run(dataset)
runner.export_markdown()
```

| Module | Key Class | Purpose |
|---|---|---|
| `models.py` | `EvalCase`, `EvalDataset`, `EvalResult` | Data models |
| `judge.py` | `LLMJudge` | Grades agent outputs using an LLM |
| `runner.py` | `EvalRunner` | Executes eval suites with concurrency + retries |
| `criteria.py` | `CORRECTNESS`, `HELPFULNESS`, `SAFETY`, `RELEVANCE` | Built-in grading criteria |

---

## Coding Standards

- **No backward compatibility** — delete old code, rename cleanly, break APIs freely. No shims.
- **Async everywhere** — every handler, service method, tool `execute()`, DB call is `async def`
- **`from __future__ import annotations`** at the top of every file (after the module docstring)
- **Type-annotate everything** — no untyped arguments or return values
- **No bare `except:`** — always catch specific exceptions
- **`app.state.*`** is the DI container — inject in lifespan, read in routes
- **`uv run` always** — never invoke `python`, `pytest`, or `ruff` directly
- **`uv` only** — never `pip install` or `pip uninstall`
- **snake_case** — files, modules, functions, variables
- New DB models → service-local `models.py` (microservices) or `serving/monolith/` (monolith)
- New skills → `src/ravi/capabilities/skills/<name>/SKILL.md` with YAML frontmatter
- **DB session dependency** — all microservice routes use `get_db_session` from `serving/shared/database/`. Never define a local `_get_db` helper.
- **Testing** — `asyncio_mode = "auto"` in `pyproject.toml`: write `async def test_*` directly, no `@pytest.mark.asyncio` needed.

---

## Known Tech Debt

| Area | Issue |
|---|---|
| `serving/monolith/routes/spotify_oauth.py` | `session_id = "default_user"` hardcoded — needs real user identity from auth context |
| `serving/shared/tasks/store.py` | `TaskStore` is in-memory only — should be Postgres-backed for persistence across restarts |
| `agents/assistant/agent.py` | `_run_inner()` is ~200 lines — guardrail checks should be extracted into helpers |
| Test coverage | Gaps in: guardrails, middleware, MCP adapter, most microservices, evals |
