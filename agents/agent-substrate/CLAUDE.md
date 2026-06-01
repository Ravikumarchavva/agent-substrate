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

> **Note:** sections below this directory map predate the actor-model + layered
> migration and may reference deleted modules (`extensions/`, `kernel/messages`,
> `BaseAgent`, `BaseModelClient`, `ReActAgent`). Trust the tree above and the
> import-linter contract in `pyproject.toml` over the stale examples below until
> they are rewritten.

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

`ravi/kernel/` is the contract layer. Once built, it is **never edited to add capability**.

**Dependency rule** (strictly downward; enforced by `uv run lint-imports`):

```
orchestration  ←  reasoning  ←  fabric  ←  kernel        (the 4-layer cognitive stack)

adapters, catalog, evals, serving  =  orthogonal (may import down into the stack)
```

**Adding capability:**

| You want to add… | Write it in… |
|---|---|
| A new agent type | `agents/<name>/agent.py` — subclass `AssistantAgent` or follow its pattern |
| A new guardrail | `agents/guardrails/<name>.py` — implement `run(ctx) -> GuardrailResult` |
| A new LLM provider | `adapters/llm/<provider>/` — implement `LLMClient` Protocol from `kernel/llm.py` |
| A new memory backend | `adapters/memory/<name>.py` — implement `HistoryProvider` Protocol from `kernel/history.py` |
| A new tool | `capabilities/tools/<name>/tool.py` — implement `Tool` Protocol (auto-scanned) |
| A new skill | `capabilities/skills/<name>/SKILL.md` — YAML frontmatter + prompt body |

**Enforcement** (CI fails if violated):

1. `tool.importlinter` contract `kernel is independent` — `ravi.kernel` may not import from any other ravi package.
2. `tests/architecture/test_kernel_invariants.py` — LOC ceiling (15k), file-count ceiling (110), no concrete agents/guardrails/middleware in kernel.

---

## Key Patterns

### Tool Creation

```python
from ravi.kernel.tools import ToolExecutionResult, Tool
from ravi.kernel.content import TextBlock

class MyTool:
    name = "my_tool"
    description = "What it does"
    input_schema = {"type": "object", "properties": {...}, "required": [...]}

    async def execute(self, *, param: str) -> ToolExecutionResult:  # type: ignore[override]
        return ToolExecutionResult(content=[TextBlock(text="result")])
```

Tools are auto-discovered by `capabilities/internal/scanner.py` when placed in `capabilities/tools/<name>/tool.py`. No registration decorator needed.

### LLM Client

```python
from ravi.adapters.llm.factory import create_model_client
client = create_model_client("gpt-4o", api_keys={"openai": "..."})
```

### MCP Tools — load at runtime via MCPClient

```python
from ravi.adapters.mcp import MCPClient

client = MCPClient(url="http://localhost:9000/sse")
tools = await client.discover_tools()   # returns list[MCPTool]
```

### Shared Event Bus — always use factory functions

```python
from ravi.serving.shared.events.bus import EventBus
from ravi.serving.shared.events.types import workflow_started, workflow_failed

bus: EventBus = app.state.bus
await bus.publish(workflow_started(job_id=job.id, run_id=run.id))
```

### SSE Event Bus (monolith only)

```python
from ravi.serving.monolith.sse.bridge import WebHITLBridge

bridge: WebHITLBridge = request.app.state.bridge
await bridge.put_event({"type": "my_event", "data": {...}})
```

### New Route (monolith)

1. Create `serving/monolith/routes/my_feature.py` with `router = APIRouter(prefix="/my-feature")`
2. Mount in `serving/monolith/app.py → create_app()` via `app.include_router(...)`

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

### Memory Architecture — CRITICAL

- All `HistoryProvider` methods are `async def`. **Always `await` them.**
- Lifecycle: append messages → get messages → clear.

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
| `serving/monolith/routes/spotify_oauth.py` | `session_id = "default_user"` hardcoded in 5 places | Should use real user identity from auth context (XSS/CSRF issues fixed in Phase 3) |
| `serving/shared/tasks/store.py` | `TaskStore` is in-memory only | Should be backed by Postgres for persistence across restarts |
| `agents/assistant/agent.py` | `_run_inner()` is ~200 lines | Needs guardrail checks extracted into helper methods |
| Test coverage | No tests for agents, context, skills, evals, HITL, most microservices | Major gap — only storage, structured, pipelines, event_bus are tested |

