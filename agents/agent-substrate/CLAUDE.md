# Agent Framework — Claude Instructions

This file is read automatically by Claude when working in this repo.
Trust it as the primary reference; only search the codebase if something here is incomplete or appears incorrect.

**Deeper knowledge lives in [`docs/claude_docs/`](docs/claude_docs/CLAUDE.md)** —
architecture rationale, an honest prioritized roadmap, recorded decisions, and
debugging playbooks. This file (root CLAUDE.md) stays a fast orientation
reference; `docs/claude_docs/` is where the *why*, the *what's actually next*,
and the *how do I debug this specific class of problem* live. Read its index
before starting non-trivial work, and update it as you learn things worth
keeping — it decays like this file does if left untouched.

---

## Project Summary

Python async AI-agent framework with two deployment modes:

1. **Monolith** — single FastAPI server at `src/substrate/serving/monolith/`
2. **Microservices** — 12 independent FastAPI services at `src/substrate/serving/services/`

Stack: Python 3.13, FastAPI, SQLAlchemy 2 async, asyncpg, PostgreSQL 18, Redis 7, OpenTelemetry → Tempo.

Package manager: **`uv`** (never `pip`).

---

## Bootstrap & Run

```bash
# Install dependencies (always first)
uv sync

# Start infrastructure (Postgres, Redis, MinIO, observability, MCP server)
make infra-up

# Start monolith backend (port 8000)
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
agent-substrate/                         ← repo root
├── src/substrate/                 ← Python package (all application code)
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
src/substrate/
├── kernel/       L0 — FROZEN. Pure contracts: Protocols, dataclasses, enums. No I/O.
│   ├── core/             content.py (ContentBlock, TextBlock, ChatMessage, ToolUseBlock, …),
│   │                     identity.py (AgentId, TopicId), usage.py (Usage), errors.py
│   ├── llm/              llm.py — LLMClient, EmbeddingClient Protocols
│   ├── messaging/        message.py (Message, Subscription), stream.py (TextDelta,
│   │                     ReasoningDelta, CompletionEvent, StreamDone, AgentProgress)
│   ├── storage/          blob.py (BlobStore), history.py (HistoryProvider),
│   │                     vector.py (VectorStore, Document), graph.py (GraphStore),
│   │                     memory.py (SessionStore)
│   ├── tools/            tools.py (Tool/HostedTool/ProviderDefinedTool, AnyTool,
│   │                     ToolRegistry, ToolRisk, ToolExecutionResult), chain.py (chain
│   │                     contracts: ChainPolicy, InvocationResult, ChainRunResult),
│   │                     skills.py, approval.py (ApprovalHandler, ApprovalResult)
│   ├── agent/            context.py (CompactionStrategy), middleware.py (Interceptor),
│   │                     supervision.py (Supervision, SpawnBudget, Priority),
│   │                     runtime_context.py (RunMeta)
│   └── runtime/          agent.py (Agent), inbox.py, scheduler.py, supervisor.py,
│                         effects.py, fanout.py, follow_graph.py, log_entry.py, ids.py, …
│
├── agents/       L1 — core intelligence: agent types, middleware, guardrails, context
│   ├── core/             ReActAgent, OrchestratorAgent (+ SubAgentConfig), UserProxyAgent,
│   │                     InformationAgent, PersonalFeedAgent
│   ├── context/          AgentContext, InMemoryHistoryProvider, compaction/ strategies
│   ├── llm/              model registry, SemanticCache, FallbackClient, ModelRouter
│   ├── middleware/       MiddlewarePipeline, guardrails/, AuditLogger, RateLimiter, …
│   ├── runtime/          Runtime facade + Worker + backends/ (in-process asyncio dispatch)
│   ├── tools/            Toolbox (ToolRegistry impl), ToolInvoker (chain dispatch, L1)
│   ├── storage/          InMemoryFileStore, TaskStore/GlobalTaskStore
│   ├── hooks/            lifecycle hooks (RUN_START/END, STEP, LLM, TOOL, HANDOFF)
│   ├── resources/        ExecutionTracker (per-agent spend, wired into ReActAgent loop)
│   ├── supervision/      SpawnTracker (headcount + priority preemption), RetryPolicy
│   └── factory.py        create_assistant_agent, load_session_memory, rebuild_messages
│
├── capabilities/ L2 — everything agents can use: tools, knowledge, memory, history, …
│   ├── llm/              OpenAIChatCompletionClient — universal /v1/chat/completions client
│   ├── tools/            tool implementations + skills + discovery scanner
│   │   ├── skills/       SKILL.md prompt-skill packages (SkillTool, SkillManager)
│   │   ├── chain/        ToolChainTool + bridge + prelude (sandboxed code-mode chaining)
│   │   ├── web/          WebSearchTool, WebSurferTool, ReadUrlTool, WikipediaTool
│   │   ├── files/        DocumentAnalyzerTool, InvoiceExtractorTool
│   │   ├── communication/EmailSenderTool, HttpRequestTool
│   │   ├── compute/      CalculatorTool
│   │   ├── database/     PostgresQueryTool (queries arbitrary user DBs)
│   │   ├── ai/           ImageGeneratorTool, KnowledgeSearchTool
│   │   ├── utils/        CurrentTimeTool, ToolSearchTool
│   │   ├── task_manager/ TaskManagerTool (Kanban board)
│   │   └── code_interpreter/ CodeInterpreterTool (Firecracker VM / K8s — explicit opt-in)
│   ├── knowledge/        RAGPipeline, GraphRAGPipeline, chunkers, reranker, loaders/
│   ├── memory/           RedisSessionStore, DurableMemoryStore
│   ├── history/          RedisHistoryProvider, DurableHistoryProvider
│   ├── vector/           PgVectorStore  (implements VectorStore Protocol)
│   ├── graph/            AGEGraphStore  (implements GraphStore Protocol)
│   ├── storage/          S3FileStore (wraps infrastructure MinIOConnector)
│   ├── pipeline/         PipelineEngine, DataRef/DataRefArtifactStore, PipelineStore
│   └── triggers/         TriggerScheduler, WebhookRegistry, ConditionMonitor
│
├── fabric/       L3 — how agents are orchestrated: flows + evals
│   ├── flows/            SequentialFlow, ParallelFlow, ConditionalFlow
│   └── evals/            EvalCase, EvalDataset, LLMJudge, EvalRunner, EvalReport
│
├── integrations/ external third-party I/O adapters (orthogonal to layers)
│   ├── llm/              LLMFactory, provider clients (openai/, anthropic/, gemini/), encoders/
│   ├── tools/            protocol bridges — MCP (MCPClient, MCPTool), A2A (planned)
│   ├── events/           EventBus (Redis pub/sub) + EventEnvelope (wire format)
│   └── connectors/       external service connectors (email, google_calendar)
│
├── infrastructure/ built-in standard backends for the engine itself (orthogonal to layers)
│   ├── database/         PostgresConnector (asyncpg pool — engine's own DB)
│   ├── cache/            RedisConnector
│   ├── storage/          MinIOConnector (S3-compatible object storage)
│   └── runtime/          EventLog/Inbox/Scheduler, RedisJournal,
│                         build_postgres_runtime() — durable runtime backends
│
├── serving/      deployment shells (orthogonal to layers)
│   ├── monolith/         single FastAPI app (app.py, routes/, sse/, security/, services/)
│   ├── services/         12 independent microservices (one FastAPI app per folder)
│   ├── shared/           cross-service infra: auth, database, events, contracts, observability
│   ├── protocol/         engine↔UI SSE wire protocol (WireEvent union, requests, version);
│   │                     `from_log.wire_from_log(kind, payload)` converts log entries to WireEvents
│   └── stream/           AgentStreamSession — tails EventLog, maps entries via wire_from_log
│
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

## Architecture — four enforced layers

```
kernel (L0)       Pure contracts: Protocols, dataclasses, enums. No I/O.
    ↑ imported by
agents (L1)       Core intelligence: LLM loop, guardrails, middleware, agent types.
    ↑ imported by
capabilities (L2) What agents can do: tools, skills, knowledge/RAG, memory, stores.
    ↑ imported by
fabric (L3)       How agents are orchestrated: flows, evals, durable execution.
```

`integrations/`, `infrastructure/`, and `serving/` are **orthogonal** — they
implement kernel Protocols and wire all layers together in lifespan. They are not
part of the stack hierarchy. Distinction: `infrastructure/` holds built-in
standard backends the engine runs on (Postgres, Redis, MinIO + durable runtime);
`integrations/` holds external third-party adapters (LLM providers, MCP,
email/calendar connectors).

**Dependency rule** (strictly downward; enforced by `uv run lint-imports`):

```
fabric        →  capabilities  →  agents  →  kernel
integrations, infrastructure, serving  =  orthogonal (cross-layer by design)
```

**Import-linter contracts** (`pyproject.toml`, CI fails if violated):

| Contract | Rule |
|---|---|
| `four stack layers` | Each layer only imports from the layer(s) below it |
| `agents cannot import capabilities or fabric` | L1 must not reach up to L2 or L3 |
| `capabilities cannot import fabric` | L2 must not reach up to L3 |
| `kernel is independent` | L0 imports nothing from the rest of the codebase |

**Kernel invariants** (`tests/architecture/test_kernel_invariants.py`):
- LOC ceiling (6k) and file-count ceiling (45) — catch accidental feature drift
- No concrete implementations — only Protocols, ABCs, dataclasses, enums

`src/substrate/kernel/` is **frozen** — new contracts belong there only if they have zero external dependencies and are needed by multiple layers. New capabilities go in `capabilities/`, new agent behaviour in `agents/`, new orchestration in `fabric/`.

---

## Key Patterns

### Adding capability

| You want to add… | Write it in… |
|---|---|
| A new agent type | `agents/core/<name>.py` — follow `ReActAgent` pattern |
| A new guardrail | `agents/middleware/guardrails/<name>.py` — implement middleware contract |
| A new LLM provider | `integrations/llm/<provider>/` — implement `LLMClient` Protocol from `kernel/llm/llm.py` |
| A new memory backend | `capabilities/history/<name>.py` — implement `HistoryProvider` Protocol from `kernel/storage/history.py` |
| A new vector store | `capabilities/vector/<name>.py` — implement `VectorStore` Protocol from `kernel/storage/vector.py` |
| A new graph store | `capabilities/graph/<name>.py` — implement `GraphStore` Protocol from `kernel/storage/graph.py` |
| A new tool | `capabilities/tools/<name>/tool.py` — implement `Tool` Protocol (auto-scanned, no registration needed) |
| A new skill | `capabilities/tools/skills/<name>/SKILL.md` — YAML frontmatter + prompt body |
| A new agent flow | `fabric/flows/` — write a standalone agent (`id` + `run(ctx, inbox)`) using SequentialFlow / ParallelFlow / ConditionalFlow |

### Tool creation

```python
from substrate.kernel.tools import ToolExecutionResult
from substrate.kernel.core.content import TextBlock

class MyTool:
    name = "my_tool"
    description = "What it does"
    input_schema = {"type": "object", "properties": {...}, "required": [...]}

    async def execute(self, *, ctx=None, **kwargs) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text="result")])
```

`substrate.kernel.tools` re-exports the full taxonomy: `Tool` (LOCAL, `execute()`),
`HostedTool` (provider-executed, `provider_specs`), `ProviderDefinedTool`
(provider call-shape + local `handle_call()`). Use `is_hosted_tool` /
`is_provider_defined_tool` to branch at dispatch. Wire-dict encoding for each
provider lives in `integrations/llm/<provider>_client.py::_tools_from_options`
+ `integrations/llm/encoders/<provider>.py::encode_tools` — no shared kernel
encoder type; each provider client builds its own dicts.

Placed at `capabilities/tools/my_tool/tool.py` — `CatalogScanner` discovers it automatically.

### LLM client

```python
from substrate.integrations.llm import LLMFactory

# Auto-detects provider from model name prefix
client = LLMFactory("gpt-4o", api_key).build()
client = LLMFactory("groq/llama-3.3-70b-versatile", api_key).build()
client = LLMFactory("ollama/llama3.2", "ollama").build()   # local, no key

# Or construct the universal client directly
from substrate.capabilities.llm import OpenAIChatCompletionClient
client = OpenAIChatCompletionClient(model="llama3.2", api_key="ollama",
                                    base_url="http://localhost:11434/v1")
```

### MCP tools

```python
from substrate.integrations.tools.mcp import MCPClient, MCPTool

client = MCPClient(url="http://localhost:9000/sse")
tools = await MCPTool.from_mcp_client(client)   # returns list[MCPTool]
```

### Event bus — always use factory functions

```python
from substrate.integrations.events import EventBus
from substrate.serving.shared.events.types import workflow_started

bus: EventBus = app.state.bus
await bus.publish(workflow_started(run_id=run.id, thread_id=thread.id, user_content=text))
```

`EventBus` + `EventEnvelope` live in `integrations/events/`; the domain-event
factory functions live in `serving/shared/events/types.py`.

Never construct event dicts manually — always use the factory functions from `serving/shared/events/types.py`.

### SSE event bus (monolith only)

```python
from substrate.serving.monolith.sse.bridge import WebHITLBridge

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
from substrate.agents.context import InMemoryHistoryProvider

# Redis-backed
from substrate.capabilities.history import RedisHistoryProvider

# Postgres-backed
from substrate.capabilities.history import DurableHistoryProvider
```

All `HistoryProvider` methods are `async def`. Always `await` them.

## Knowledge / RAG

Vector and graph store contracts live in the kernel. Concrete implementations live in `capabilities/`; `capabilities/knowledge/` wires them into pipelines.

```python
# Contracts (kernel)
from substrate.kernel.storage.vector import VectorStore, Document, SearchResult
from substrate.kernel.storage.graph import GraphStore, Entity, Relationship, SubGraph

# Concrete implementations
from substrate.capabilities.vector import PgVectorStore
from substrate.capabilities.graph import AGEGraphStore

# High-level RAG pipeline
from substrate.capabilities.knowledge import RAGPipeline, GraphRAGPipeline

pipeline = RAGPipeline(embedding_client=embed_client, vector_store=pg_store)
await pipeline.ingest("Long document …", collection="kb")
results = await pipeline.query("What is X?", collection="kb")
```

`Document` (RAG text chunk) and `DocumentBlock` (LLM message content) are distinct — never conflate them.

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

# CORS (comma-separated origins)
CORS_ALLOWED_ORIGINS=http://localhost:3000,http://localhost:3002

# Observability
OTLP_ENDPOINT=http://localhost:4318

# Auth
JWT_SECRET=<32+ char random string — required>

# Code interpreter (optional)
CODE_INTERPRETER_URL=...

# Agent runtime backend: "postgres" (default, durable) or "memory" (in-process, no infra)
RUNTIME_BACKEND=postgres

# Durable runtime's own asyncpg pool (separate from the ORM engine's pool)
RUNTIME_PG_POOL_MIN_SIZE=2
RUNTIME_PG_POOL_MAX_SIZE=10
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
| Monolith backend | 8000 | `uv run start` |
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
from substrate.logger import setup_logging
logger = setup_logging("substrate.my_module")
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

## Evaluation Framework (`fabric/evals/`)

```python
from substrate.fabric.evals import EvalCase, EvalDataset, LLMJudge, EvalRunner, CORRECTNESS

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
- New skills → `src/substrate/capabilities/tools/skills/<name>/SKILL.md` with YAML frontmatter
- **DB session dependency** — all microservice routes use `get_db_session` from `serving/shared/database/`. Never define a local `_get_db` helper.
- **Testing** — `asyncio_mode = "auto"` in `pyproject.toml`: write `async def test_*` directly, no `@pytest.mark.asyncio` needed.
- **Interactive Console** — `/q` is the sole quit/exit command. Interactive session uses `prompt_toolkit` for async-compatible autocomplete of slash commands (`/tools`, `/skills`, `/reset`, `/help`, `/q`) and input history.

---

## Known Tech Debt

| Area | Issue |
|---|---|
| Test coverage | `guardrails`/`middleware`/MCP adapter/`fabric/evals` have real (if not exhaustive) coverage as of 2026-07-05 — the genuinely thin area is **microservices business logic** (`identity`, `policy`, `job_controller`, `tool_executor`, `code_interpreter`): only health/smoke tests exist (`tests/server/test_services_health.py`), no per-service behavior tests. See `docs/claude_docs/roadmap.md` "Recently shipped" (v1 remediation) for what else shipped that session and its known gaps. |
| Microservices event architecture | Only 3 of ~28 domain-event factories in `serving/shared/events/types.py` have a real producer (`session_started`, `workflow_started`, `workflow_failed`); `live_stream` (the SSE projector) has almost nothing to project in the microservices deployment beyond a run starting/failing. Concretely: `workflow_completed` is never published by any service, so `job_controller::complete_run` is unreachable — a successful run has no code path that marks it `completed`. See `docs/claude_docs/roadmap.md`'s deferred-items list (2026-07-12 entry) for the full finding. |

---

## Behavioral Guidelines

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed. These guidelines apply to the developer coding level and the agent work level as well.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

### 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

### 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

### 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

### 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
