# Agent Framework — Claude Instructions

This file is read automatically by Claude when working in this repo.
Trust it as the primary reference; only search the codebase if something here is incomplete or appears incorrect.

---

## Project Summary

Python async AI-agent framework with **two deployment modes**:

1. **Monolith** — single FastAPI server at `src/ravi/server/`
2. **Microservices** — 12 independent FastAPI services under `src/ravi/services/`

Stack: Python 3.13, FastAPI, SQLAlchemy 2 async, asyncpg, PostgreSQL 18, Redis 7, OpenTelemetry → Tempo.

Package manager: **`uv`** (never `pip`).

---

## Bootstrap & Run

```bash
# Install dependencies (always first)
uv sync

# Start infrastructure
make infra-up

# Optional: MCP SSE demo server (needed for examples 04/05/06)
docker compose -f deployment/docker/docker-compose.yml --profile mcp up -d mcp-server   # → localhost:9000/sse

# Start monolith backend
uv run uvicorn ravi.server.app:app --port 8000 --reload

# Run tests
uv run pytest

# Lint / format
uv run ruff check .
uv run ruff format .
```

---

## Full Directory Map

```
ravi/                            ← repo root
├── src/ravi/                    ← Python package (all application code)
├── deployment/                    ← All deployment artefacts
│   ├── docker/                    ← Dockerfiles + Compose files
│   │   ├── backend.Dockerfile     ← Production backend image
│   │   ├── code-interpreter.Dockerfile
│   │   ├── docker-compose.yml     ← Local dev (monolith + infra)
│   │   ├── docker-compose.microservices.yml
│   │   ├── mcp_server/            ← FastMCP 2.x demo SSE server
│   │   └── tempo/                 ← Tempo OTLP config
│   └── k8s/                       ← Kubernetes / Kustomize manifests
│       ├── base/                  ← Production-ready base (kustomize base)
│       └── overlays/kind/         ← Kind cluster overlay + smoke-test.ps1
├── deploy.py                      ← Cross-platform Kind deploy script
├── docs/                          ← Architecture, operations, design patterns
├── examples/                      ← Jupyter notebooks (01–14)
├── tests/                         ← pytest suite
└── pyproject.toml                 ← uv project + ruff + pyright config
```

```
src/ravi/
├── kernel/                    ← FROZEN. Contracts (ABCs, Protocols, dataclasses, enums) + minimal reference impls.
│   ├── agents/                ← BaseAgent ABC + AgentRunResult dataclasses (concrete agents in extensions/)
│   ├── memory/                ← BaseMemory ABC + UnboundedMemory reference (SessionManager in extensions/)
│   ├── tools/                 ← BaseTool ABC + ResettableTool protocol + @tool decorator (builtins in extensions/)
│   ├── context/               ← ModelContext ABC only (concrete strategies in extensions/)
│   ├── messages/              ← SystemMessage / UserMessage / AssistantMessage / ToolCallMessage / encoders
│   ├── guardrails/            ← BaseGuardrail ABC only (PII, ContentFilter, etc. in extensions/)
│   ├── middleware/            ← BaseMiddleware ABC + MiddlewarePipeline (builtins in extensions/)
│   ├── pipelines/             ← Pipeline schema dataclasses only (runner in extensions/)
│   ├── storage/               ← FileStore ABC + LocalFileStore + Document + TenantContext
│   ├── structured/            ← StructuredOutputResult + schemas (parse/judge/router in extensions/)
│   ├── llm/                   ← BaseModelClient ABC + ProviderConfig + model registry
│   ├── runtime/               ← AgentRuntime protocol + LocalRuntime reference + LocalRuntime internals
│   ├── agent_catalog/         ← AgentCatalog (FQN registry) + SkillManagerProtocol
│   ├── plugin/                ← Decorator registry: @register_agent, @register_guardrail, etc.
│   ├── execution/             ← ExecutionContext + ExecutionMiddlewarePipeline
│   └── batch/                 ← BatchConfig / BatchItem / BatchResult (BatchProcessor in extensions/)
│
├── extensions/                ← Built-in feature implementations over kernel contracts.
│   ├── agents/                ← ReActAgent, OrchestratorAgent, Agent, FlowAgent, GraphAgent, RuntimeAgent
│   ├── guardrails/            ← PIIDetectionGuardrail, ContentFilterGuardrail, PromptInjection, MaxToken, LLMJudge, ToolCallValidation, run_guardrails()
│   ├── middleware/            ← AuditLogger, Cache, Retry, RateLimiter, GuardrailsMiddleware, HistoryTruncator, …
│   ├── memory/                ← SessionManager + SessionState orchestration
│   ├── context/               ← SlidingWindowContext, RedisModelContext, HybridContext, TokenBudgetContext, SummarizingContext
│   ├── llm/                   ← CachedModelClient, FallbackClient, ModelRouter, SemanticCache
│   ├── structured/            ← parse(), LLMJudge, StructuredRouter
│   ├── extraction/            ← Extractor + Invoice/Receipt/Contract schemas
│   ├── rag/                   ← VectorStore, GraphStore, chunkers, loaders, RAG pipeline, reranker
│   ├── pipelines/             ← PipelineRunner, WhilePipelineRunner, workflow middleware, codegen
│   ├── batch/                 ← BatchProcessor
│   ├── storage/               ← EncryptedFileStore, factory, key providers
│   ├── tools/                 ← Built-in CalculatorTool, WebSearchTool, GetBitcoinPriceTool, GetCurrentTimeTool
│   └── resilience/            ← Retry / timeout / circuit-breaker policies
│
├── integrations/              ← External/SDK-backed adapters
│   ├── llm/
│   │   └── openai/            ← OpenAIClient — text, vision, STT, TTS, Realtime S2S, image gen
│   ├── mcp/                   ← MCPClient, MCPTool wrappers, MCP App tools, app_tool_base
│   ├── memory/                ← Concrete memory backends
│   │   ├── redis_memory.py    ← RedisMemory (uses redis.asyncio)
│   │   └── postgres_memory.py ← PostgresMemory (uses sqlalchemy + asyncpg)
│   ├── skills/                ← Backward-compat re-exports (SkillManager, SkillLoader now in catalog/)
│   ├── spotify/               ← SpotifyService, SpotifyAuthService
│   └── storage/               ← S3FileStore (S3-compatible backend, uses aiobotocore)
│
├── catalog/                   ← Unified capability system (tools, skills, connectors, pipelines)
│   ├── tools/                 ← BaseTool implementations (human_input, task_manager, web_surfer, …)
│   │   ├── human_input/       ← AskHumanTool, HITL handlers
│   │   ├── task_manager/      ← TaskManagerTool (Kanban board)
│   │   ├── web_surfer/        ← WebSurferTool (web browsing)
│   │   ├── code_interpreter/  ← CodeInterpreterTool (Firecracker VM HTTP client)
│   │   └── …                  ← capability_search, file_manager, email_sender, etc.
│   ├── skills/                ← SKILL.md prompt-skill packages (debugging, code_review, …)
│   ├── connectors/            ← External service connectors (email, postgres_query, …)
│   ├── _chain_runtime.py      ← ChainRuntime + AdapterProxy (Proxy pattern)
│   ├── _scanner.py            ← Convention discovery scanner (anchors on last ravi in path)
│   ├── _data_ref.py           ← DataRef / DataRefStore
│   ├── _pipeline.py           ← PipelineDef, PipelineEngine, PipelineStore
│   ├── _skill_loader.py       ← SkillLoader (filesystem scanner + YAML parser)
│   ├── _skill_manager.py      ← SkillManager (discovery + system-prompt injection)
│   ├── _skill_models.py       ← SkillMetadata, Skill (pure data classes)
│   ├── _temporal/             ← Temporal.io workflow integration (activities, worker, workflows)
│   └── _triggers/             ← Trigger system (conditions, scheduler, webhooks)
│
├── server/                    ← Monolith FastAPI server
│   ├── app.py                 ← Factory + lifespan; DI via app.state.*
│   ├── _lifespan.py           ← Extracted init functions (init_llm_clients, init_infrastructure, init_tool_registry, init_runtime_services)
│   ├── routes/                ← 18 route files (chat, tasks, hitl, threads, mcp_apps, workflows, triggers, …)
│   ├── security/              ← Thin wrappers delegating to shared/auth/ (binds settings)
│   ├── services/              ← Business logic (thread_service.py, agent_service.py, …)
│   ├── sse/                   ← SSE event bus + HITL bridge (monolith only)
│   │   ├── bridge.py          ← WebHITLBridge, BridgeRegistry
│   │   └── events.py          ← EventBus + typed SSE event dataclasses
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
│   ├── tool_executor/         ← Executes individual tools in isolated contexts
│   └── code_interpreter/      ← Firecracker VM sandbox for code execution
│
├── shared/                    ← Cross-service infrastructure and contracts
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
│   │   └── types.py           ← Event factories: workflow_started, workflow_completed, …
│   ├── auth/                  ← Canonical JWT + auth middleware (AuthClaims, verify_token, get_current_user)
│   ├── database/              ← Shared session factory + get_db_session dependency
│   ├── observability/         ← OpenTelemetry setup + telemetry (logger instance re-exported here; setup_logging() lives in ravi/logger.py)
│   └── tasks/                 ← In-memory TaskStore singleton
│
├── configs/settings.py        ← Pydantic Settings (reads from .env)
├── evals/                     ← Evaluation framework (runner, judge, criteria, models)
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
| `code_interpreter` | — | Firecracker VM sandbox for code execution |

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

## Frozen kernel — `ravi.kernel` is stable forever

`ravi/kernel/` is the contract layer. Once built, it is **never edited to add capability**. New features live in `ravi/extensions/`.

**Dependency rule** (strictly downward; enforced by `uv run lint-imports`):

```
server | services  ←  catalog  ←  integrations  ←  extensions  ←  kernel
```

**Adding capability:**

| You want to add… | Write it in… | How |
|---|---|---|
| A new agent type | `extensions/agents/<name>/agent.py` | `@register_agent("<name>")` decorator |
| A new guardrail | `extensions/guardrails/<name>.py` | `@register_guardrail("<name>")` |
| A new middleware | `extensions/middleware/<name>.py` | `@register_middleware("<name>")` |
| A new LLM provider | `integrations/llm/<provider>/` | Subclass `BaseModelClient`, register via factory |
| A new memory backend | `integrations/memory/<backend>.py` | Subclass `BaseMemory`, wire in lifespan |
| A new context strategy | `extensions/context/<name>.py` | `@register_context("<name>")` |
| A new tool | `catalog/tools/<name>/tool.py` | Convention-scanned (no decorator needed) |

**Enforcement** (CI fails if violated):

1. `tool.importlinter` contract `kernel is independent` — `ravi.kernel` may not import from `ravi.{extensions, integrations, catalog, server, services, shared, configs, logger}`.
2. `tests/architecture/test_kernel_invariants.py` — LOC ceiling (15k), file-count ceiling (110), no concrete agents/guardrails/middleware in kernel.

To add a new plugin category, extend `kernel/plugin/registry.py` with one `_make_decorator(...)` line. Everything else is downward.

---

## Key Patterns

### Tool Creation — always subclass `BaseTool`

```python
from ravi.core.tools.base_tool import BaseTool, ToolResult

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
from ravi.integrations.llm.openai.openai_client import OpenAIClient

# Abstract base is now in core:
from ravi.core.llm.base_client import BaseModelClient
```

### MCP Tools — load at runtime via MCPClient

```python
from ravi.integrations.mcp import MCPClient

client = MCPClient(url="http://localhost:9000/sse")
tools = await client.discover_tools()   # returns list[MCPTool]
```

There is **no** `integrations.mcp.loader` module. Do not import from it.

### Shared Event Bus — always use factory functions

```python
from ravi.shared.events.bus import EventBus
from ravi.shared.events.types import workflow_started, workflow_failed

bus: EventBus = app.state.bus
await bus.publish(workflow_started(job_id=job.id, run_id=run.id))
```

Never construct event dicts manually — always use the factory functions from `shared/events/types.py`.

### SSE Event Bus (monolith only)

```python
from ravi.server.sse.bridge import WebHITLBridge

bridge: WebHITLBridge = request.app.state.bridge
await bridge.put_event({"type": "my_event", "data": {...}})
```

### New Route (monolith)

1. Create `server/routes/my_feature.py` with `router = APIRouter(prefix="/my-feature")`
2. Mount in `server/app.py → create_app()` via `app.include_router(...)`

---

## Memory — `RedisMemory`

```python
from ravi.core.memory import RedisMemory

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
| `UserMessage` | `list[MediaType]` | List of `str`, `Image.Image`, `ImageContent`, `AudioContent`, `VideoContent` |
| `AssistantMessage` | `Optional[list[MediaType]]` | List or `None` (tool-call-only turn) |
| `ToolExecutionResultMessage` | `str` | Plus `tool_call_id`, `name` fields |

`ToolCallMessage` fields: `tc.name`, `tc.arguments` (dict) — **not** `tc.function['name']`.

### `ImageContent` — image inputs without loading into PIL

```python
from ravi.core.messages import ImageContent

# URL (public or presigned)
ImageContent(url="https://example.com/photo.jpg", detail="high")

# Files-API ID
ImageContent(file_id="file-abc123")

# Raw bytes
ImageContent(data=b"...", media_type="image/jpeg")
```

`detail` values: `"low"` | `"high"` | `"original"` | `"auto"` (default).

### Image generation

```python
urls = await client.generate_image("a cat in a spacesuit")

# gpt-image-1 (quality: "low"/"medium"/"high"/"auto")
urls = await client.generate_image(
    "product shot on white",
    model="gpt-image-1",
    size="1024x1024",
    quality="high",
)
```

Check `client.supports_image_generation` before calling. Returns `list[str]` (URLs or `data:image/png;base64,...`).

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
| Monolith backend | 8000 | `uv run uvicorn ... --port 8000` |
| Tempo | 4318 | OTLP HTTP |
| Grafana | 3001 | Dashboard |

Microservice ports: see `deployment/docker/docker-compose.microservices.yml`.

---

## Observability Stack (Kind cluster)

All observability services run in `af-observability` namespace.
Deploy: `kubectl apply -k deployment/k8s/overlays/kind/` (includes observability)

| Component | Image | Internal Port | Purpose |
|---|---|---|---|
| Loki | `grafana/loki:2.9.4` | 3100 | Log aggregation |
| Promtail | `grafana/promtail:2.9.4` | DaemonSet | Scrapes pod logs → Loki |
| Tempo | `grafana/tempo:2.3.1` | 3200, 4317, 4318 | Distributed tracing (OTLP) |
| Prometheus | `prom/prometheus:v2.49.1` | 9090 | Metrics collection |
| Grafana | `grafana/grafana:10.3.1` | 3000→3001 | Dashboards at `http://localhost/grafana/` |
| Node Exporter | `prom/node-exporter:v1.7.0` | 9100 | Host-level metrics |

### Access
- **Grafana**: `http://localhost/grafana/` (admin/admin, anonymous read)
- **Pre-provisioned datasources**: Loki, Tempo, Prometheus (auto-configured)
- **Pre-provisioned dashboards**: "Services Overview" + "Frontend Logs"

### Traces
All services send OTLP traces to `http://tempo.af-observability.svc.cluster.local:4318`.
Set via `OTLP_ENDPOINT` env var (injected by kustomize patch for Kind).

### Logs
- Backend services output structured JSON via `ravi/logger.py` → Promtail scrapes stdout
- Frontend sends warn/error logs to `/api/logs` → structured JSON stdout → Promtail
- Query in Grafana via Loki: `{namespace=~"af-.*"}`
- Logging convention in Python modules:
    - `from ravi.logger import setup_logging`
    - `logger = setup_logging()`
    - Do not call `logging.getLogger(...)` directly in app modules
    - `setup_logging()` configures the `ravi` namespace once and uses rotating files by default

---

## CI/CD

GitHub Actions workflows in `.github/workflows/ci.yml`:
- **Lint**: Ruff check + format
- **Type check**: Pyright (soft-fail)
- **Test**: pytest with Postgres + Redis services
- **Build**: Docker image to GHCR
- **Security**: pip-audit

---

## Evaluation Framework (`evals/`)

Measures agent quality via LLM-as-judge grading.

| Module | Key Class | Purpose |
|---|---|---|
| `models.py` | `EvalCase`, `EvalDataset`, `EvalResult`, `EvalReport` | Data models for eval definition and results |
| `judge.py` | `LLMJudge` | Grades agent outputs using an LLM |
| `runner.py` | `EvalRunner` | Executes eval suites with concurrency + retries |
| `criteria.py` | `CORRECTNESS`, `HELPFULNESS`, `SAFETY`, `RELEVANCE` | Built-in grading criteria |

```python
from ravi.evals import EvalCase, EvalDataset, LLMJudge, EvalRunner, CORRECTNESS

runner = EvalRunner(agent=my_agent, judge=LLMJudge(criteria=[CORRECTNESS]))
report = await runner.run(dataset)
runner.export_markdown()  # writes results/report.md
```

---

## Smoke Tests

Run cluster smoke tests:
```powershell
./deployment/k8s/overlays/kind/smoke-test.ps1
```
Tests pod health, endpoints, chat flow, and observability stack.

---

## Design Patterns

> Full catalogue with file locations and anti-patterns organized by category: [`docs/design_patterns.md`](docs/design_patterns.md)

### Creational (Object Creation)

| Pattern | Location | Rule |
|---|---|---|
| **Factory Method** | `core/storage/factory.py` | Use `create_file_store(settings)` — never import concrete store classes directly. |
| **Registry** | `core/tools/catalog.py` | Register tools via `catalog.register_tool(tool, category=..., tags=[...])`. Search is global. |
| **Convention Discovery** | `catalog/_scanner.py` | Walks `catalog/tools/`; anchors on **last** `ravi` in path (Windows fix). |

### Structural (Object Composition)

| Pattern | Location | Rule |
|---|---|---|
| **Abstract Base Class** | `core/storage/base.py`, `core/agents/base_agent.py` | Subclasses implement contracts via abstract methods. |
| **Adapter** | `integrations/mcp/` | `MCPTool.get_schema()` → framework, `get_openai_schema()` → OpenAI, `get_mcp_schema()` → MCP. |
| **Proxy** | `catalog/_chain_runtime.py` | `ChainRuntime` builds tool namespace from `CapabilityRegistry`. |
| **Decorator** | `core/storage/encrypted.py` | `EncryptedFileStore` wraps any `FileStore` for transparent encryption. |

### Behavioral (Object Interaction)

| Pattern | Location | Rule |
|---|---|---|
| **Template Method** | `core/tools/base_tool.py` | Subclass `BaseTool`, implement `execute()` with `# type: ignore[override]` for keyword-only params. |
| **Strategy** | `core/tools/base_tool.py` | Set `risk = ToolRisk.CRITICAL` and `hitl_mode = HitlMode.BLOCKING` as class-level attributes. |
| **Observer/Event Bus** | `server/sse/events.py`, `shared/events/types.py` | Always use factory functions (`workflow_started(...)`) — never build event dicts manually. |
| **Protocol duck typing** | `core/agents/base_agent.py` | `PromptEnricher` is `@runtime_checkable` — `core/` stays free of `integrations/` imports. |
| **Pipeline Builder** | `core/pipelines/runner.py` | JSON graph → live objects via topology detection. |
| **ReAct Agent Loop** | `core/agents/react_agent.py` | Think → Act → Observe; guardrails at INPUT, OUTPUT, TOOL_CALL injection points. |

### Architectural (System-wide)

| Pattern | Location | Rule |
|---|---|---|
| **DI via `app.state`** | `server/app.py` | Mount all shared objects in `lifespan`. Read via `request.app.state.*` in routes. No global singletons. |

---

## Coding Standards

- **No backward compatibility** — never add shims, deprecation warnings, or legacy code paths unless explicitly requested. Delete old code, rename cleanly, break APIs freely.
- **Async everywhere** — every handler, service method, tool `execute()`, DB call is `async def`
- **`from __future__ import annotations`** at the top of every file
- **Type-annotate everything** — no untyped arguments or return values
- **No bare `except:`** — always catch specific exceptions
- **`app.state.*`** is the DI container — inject in lifespan, read in routes
- **`uv run` always** — never invoke `python`, `pytest`, `ruff`, `pyright`, or any other tool directly. Always prefix with `uv run` (e.g., `uv run pytest`, `uv run ruff check .`, `uv run pyright`). This applies in terminals, CI scripts, subagent prompts, and any automation.
- **`uv` only** — never `pip install` or `pip uninstall`
- **Snake_case** — files, modules, functions, variables
- New DB models → `server/models/`; new schemas → `server/schemas.py` (monolith) or service-local `models.py` (microservices)
- Built-in skills → `src/ravi/catalog/skills/<name>/SKILL.md` with YAML frontmatter
- MCP SSE server source → `deployment/docker/mcp_server/server.py` (FastMCP 2.x, pinned)
- Canonical enum re-exports live in `core/__init__.py` (e.g. `from ravi.core import ToolRisk, RunStatus`).
- **DB session dependency** — all microservice routes use `get_db_session` from `shared.database.dependency`. Never define a local `_get_db` helper.
- **Testing** — `asyncio_mode = "auto"` in `pyproject.toml`: no `@pytest.mark.asyncio` decorator needed. Write `async def test_*` directly.
- **Jupyter Notebook Guides** — **REQUIRED** after every change that touches a public API, new subsystem, or behaviour change. Rules:
  1. New subsystem or module → create `examples/NN_<slug>.ipynb` (next sequential number).
  2. Changed API on an existing subsystem → update the corresponding existing notebook AND add a new cell showing the new usage.
  3. Bug fix that changes observable behaviour → add a cell to the relevant notebook demonstrating correct behaviour.
  4. Notebook cells must run top-to-bottom without error (use `# type: ignore` sparingly, never skip cells).
  5. Each notebook must have a markdown cell at the top with: title, one-sentence summary, and the module path(s) being demonstrated.

---

## Known Tech Debt

| Area | Issue | Notes |
|---|---|---|
| `server/routes/spotify_oauth.py` | `session_id = "default_user"` hardcoded in 5 places | Should use real user identity from auth context (XSS/CSRF issues fixed in Phase 3) |
| `shared/tasks/store.py` | `TaskStore` is in-memory only | Should be backed by Postgres for persistence across restarts |
| `core/agents/react_agent.py` | `_run_inner()` is ~200 lines | Needs guardrail checks extracted into helper methods |
| Test coverage | No tests for agents, context, skills, evals, HITL, most microservices | Major gap — only storage, structured, pipelines, event_bus are tested |

