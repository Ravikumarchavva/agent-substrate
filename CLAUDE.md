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

# Start infrastructure (Postgres, Redis, SeaweedFS, observability, MCP server)
make infra-up                        # repo contributors — full stack via Makefile
uv run substrate up                  # anyone depending on agent-substrate as a
                                      # package — no clone, no `make`, required

# Start monolith backend (port 8000)
uv run substrate start --host 0.0.0.0 --foreground

# With hot-reload
uv run substrate start --host 0.0.0.0 --reload

# Bring up infra + start the server in one command
uv run substrate start --all --host 0.0.0.0 --foreground

# Run tests
uv run pytest

# Lint / format
uv run python -m ruff check .
uv run python -m ruff format .
```

**`make infra-up`/`make infra-down` and `uv run substrate up`/`down` are NOT
interchangeable in this repo** — they target two different docker-compose
projects. `make infra-*` uses `deployment/docker/docker-compose.yml` at the
repo root (`name: agent-framework` — what actually runs when you work in
this repo, e.g. via `make infra-up`). `substrate up`/`down` resolve to a
separate, packaged copy shipped inside the installed package
(`src/substrate/deployment/docker/docker-compose.yml`, `name:
substrate-infra`) — meant for a downstream project depending on
`agent-substrate` with no clone of this repo, not for repo contributors.
Real, hit-live bug: running `substrate down` in this repo silently did
nothing — it stopped containers under the `substrate-infra` project name,
which were never running, while the real `agent-framework-*` containers
(started via `make infra-up`) kept running with no error. **Inside this
repo, always use `make infra-up`/`make infra-down` — never `substrate
up`/`down`.**

---

## Full Directory Map

```
agent-substrate/                         ← repo root
├── src/substrate/                 ← Python package (all application code)
├── deployment/                  ← All deployment artefacts — one Compose file, three ways to use it
│   ├── docker/                  ← Dockerfiles + the one docker-compose.yml
│   │   ├── backend.Dockerfile
│   │   ├── docker-compose.yml   ← develop / one-shot run / deploy — see deployment/README.md
│   │   └── mcp_server/          ← FastMCP 2.x demo SSE server
│   └── README.md                ← How to run it (dev, one-shot, or a real public deploy)
├── docs/                        ← Architecture docs, design patterns, archive
├── examples/                    ← nine short, runnable examples (offline by default); tests/test_examples.py runs them
├── tests/                       ← pytest suite
├── legacy/                      ← Archived pre-migration code (do not import)
├── Makefile                     ← Common dev targets
└── pyproject.toml               ← uv project + ruff + import-linter config
```

```
src/substrate/
├── types/ telemetry/ tools/ models/ stores/ documents/ workspace/ safety/ context/ middleware/ runtime/ agents/ testing/
│                 THE CORE — the engine, one folder per concept, contracts beside their built-in implementation
│                 (docs/claude_docs/architecture/kernel.md). Third-party imports: pydantic,
│                 opentelemetry-api, confusable_homoglyphs, typing_extensions only. Bottom-up order:
│   ├── types/        content blocks (deep-immutable), Actor/Topic, ids, Usage, errors (stable `code`,
│   │                 `retryable`), RunScope, supervision/budgets, RunLogEntry, Wakeup, streaming events
│   ├── tools/        Tool/HostedTool/ProviderDefinedTool (protocols.py), ToolRisk, approval, chain, Toolbox
│   ├── models/       ChatModel/EmbeddingModel (protocols.py), capability registry, modality fitting,
│   │                 error classification (`classify_llm_error`), tool-argument parsing
│   ├── stores/       History/Memory/Vector/Graph/Object/Task contracts + local/ (the folder
│   │                 implementations: the durable floor) + scoped.py (tenant binding)
│   ├── documents/ workspace/ safety/    DocumentExtractor, branching/snapshots/CAS files, text normalisation
│   ├── context/      context window, compaction, `history.py` (the linear view of a thread)
│   ├── middleware/   Middleware contract + built-ins + guardrails
│   ├── runtime/      RuntimeStore (the one durable port), Runtime, Worker, Journal, RunContext, SQLite store
│   ├── agents/       RoutedAgent + @handle, ReActAgent, OrchestratorAgent, UserProxyAgent, flows, spawn limits
│   ├── telemetry/    spans/metrics at every chokepoint; GenAI + substrate.* conventions
│   └── testing/      conformance suites + doubles (never imported by production code)
│
├── evals/        eval harness (EvalCase, EvalDataset, LLMJudge, EvalRunner) — a client of the core
│
├── integrations/ adapters — everything that reaches outside the process: more storage backends,
│   │             more LLM vendors, tools, RAG, MCP, sandboxed code execution — everything
│   │             past the engine's own zero-infra defaults.
│   ├── llm/              LLMFactory (vendor auto-detect), provider clients (openai/,
│   │                     anthropic/, gemini/), encoders/
│   ├── tools/            tool implementations + skills + discovery scanner + MCP bridge
│   │   ├── mcp/          MCPClient, MCPTool — protocol bridge to external MCP servers
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
│   │   └── code_interpreter/ CodeInterpreterTool + pluggable SandboxRuntime (nsjail/k8s/inprocess)
│   ├── events/           EventBus (Redis pub/sub) + EventEnvelope (wire format)
│   ├── tts/              text-to-speech provider adapters
│   ├── knowledge/        RAGPipeline, GraphRAGPipeline, chunkers, reranker, loaders/
│   ├── memory/           RedisSessionStore, DurableMemoryStore
│   ├── vector/           PgVectorStore, LanceDB  (implement VectorStore Protocol)
│   ├── graph/            AGEGraphStore  (implements GraphStore Protocol)
│   ├── storage/          S3Connector (raw client) + S3FileStore (FileStore Protocol
│   │                     impl built on top), PgTaskStore, PostgresWorkspaceStore
│   ├── database/         PostgresConnector (asyncpg pool — engine's own DB)
│   ├── cache/            RedisConnector
│   ├── runtime/          PostgresRuntimeStore (the RuntimeStore on asyncpg)
│   ├── pipeline/         PipelineEngine, DataRef/DataRefArtifactStore, PipelineStore
│   ├── triggers/         TriggerScheduler, WebhookRegistry, ConditionMonitor
│   ├── safety/           TextSafetyClassifier, ImageSafetyClassifier (model-backed)
│   ├── artifacts/        OKF store
│   └── gdpr/             cross-store eraser
│
├── serving/      orthogonal — the one folder outside the 3-layer stack. Owns its own
│   │             composition root (factory.py/research_orchestrator.py — formerly a
│   │             separate infrastructure/ concept, eliminated).
│   ├── monolith/         single FastAPI app (app.py, routes/, sse/, security/, services/)
│   ├── services/         12 independent microservices (one FastAPI app per folder)
│   ├── shared/           cross-service infra: auth, database, events, contracts, observability
│   ├── protocol/         engine↔UI SSE wire protocol (WireEvent union, requests, version);
│   │                     `from_log.wire_from_log(kind, payload)` converts log entries to WireEvents
│   ├── stream/           AgentStreamSession — tails EventLog, maps entries via wire_from_log
│   ├── factory.py        constructs agents, tools, and runtime for the HTTP shell —
│   │                     the primary place serving/, the core and the integrations meet
│   └── research_orchestrator.py  fixed researcher/calculator/clock/coordinator topology
│                         used when AGENT_MODE=orchestrator
│
├── runtimes/     independently-deployable, heavy-dependency, HTTP-only services (orthogonal to layers)
│   ├── document_intelligence/  PaddleOCR layout/chart/table extraction + OCR (client.py always importable)
│   └── embedding_reranker/     Qwen3-VL embedding + reranking proxy service (client.py always importable)
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

## Architecture — an engine, its ports, and the adapters around it

```
substrate/{types … agents}   the core: the engine, one folder per concept, contracts beside implementations
integrations/ runtimes/      adapters: vendors, databases, MCP, tools
serving/ console/ cli        wiring: FastAPI apps, the REPL, the CLI
evals/                       the eval harness (a client of the core)
```

The core is **not** a layer of contracts: it routes messages, runs and replays durable agents,
enforces budgets and approvals, and instruments itself. Each concept's `protocols.py` (or its contract
modules) names what the engine needs from outside — an LLM client, a runtime store, a memory store… — and
carries no behaviour. The full rationale, the runtime-store/journal design and the invariants are in
[`docs/claude_docs/architecture/kernel.md`](docs/claude_docs/architecture/kernel.md); the guarantees that
are *executed* (not just described) are in `docs/claude_docs/architecture/invariants.md`, generated from
`tests/invariants/` — a test fails if it is stale.

Concepts, bottom-up (`tests/_layout.py`): `types · telemetry · tools · models · stores · documents · workspace ·
safety · context · middleware · runtime · agents`. A concept imports only the ones before it. Import a public
name from its concept package (`from substrate.runtime import Runtime`); reach into a submodule only for
something the package does not export.

**Import-linter contracts** (`pyproject.toml`; `uv run lint-imports`, CI fails if violated):

| Contract | Rule |
|---|---|
| `the core imports nothing outside it` | the core never imports integrations/runtimes/serving/evals/console/cli/config-wiring |
| `the core's concepts only import downward` | the `layers` order above; imports made only under `TYPE_CHECKING` are exempt |
| `adapters depend on the contracts and the engine's support libraries, not how it runs an agent` | integrations/runtimes may use the contracts plus `models/workspace/stores/safety/tools/runtime/telemetry`, never `agents`, the context window internals, or middleware implementations |
| `serving cannot import agents or integrations-that-were-capabilities` | serving/'s routes and services don't reach past its `factory.py`/`research_orchestrator.py` composition root |

**Structure rows** (`tests/invariants/test_structure.py`): the core's third-party imports are exactly the allowed
set; concepts only import downward; the contracts never import the engine (I27); `substrate.testing` is never imported by
production code; the public API of every concept package matches `tests/invariants/public_api.json` (change it in the
same commit as an intended API change); the core install carries `opentelemetry-api` only (the SDK/exporter belong
to the `server` extra).

New agent behaviour goes in `agents/` (declare handlers with `@handle(PayloadType)` on a `RoutedAgent`),
new orchestration in `agents/flows.py`, a new port in the concept that owns it **with a conformance suite** in
`testing/conformance/`, and a new vendor/backend/tool in `integrations/`.

---

## Key Patterns

### Adding capability

| You want to add… | Write it in… |
|---|---|
| A new agent type | `agents/<name>.py` — subclass `RoutedAgent`, declare handlers with `@handle(PayloadType)` |
| A new guardrail | `middleware/guardrails/<name>.py` — implement the middleware contract |
| A new LLM provider | `integrations/llm/<provider>/` — implement `ChatModel` (`models/protocols.py`); report a `FinishReason` and let `classify_llm_error` type its failures |
| A new memory backend | `integrations/memory/<name>.py` — implement `MemoryStore` and run `MemoryStoreConformance` against it |
| A new history backend | `integrations/history/<name>.py` — implement `ThreadStore` (`stores/threads.py`) |
| A new vector store | `integrations/vector/<name>.py` — implement `VectorStore` (`stores/vector.py`) |
| A new graph store | `integrations/graph/<name>.py` — implement `GraphStore` (`stores/graph.py`) |
| A new runtime store | implement `RuntimeStore` (`runtime/store.py`) — or a new `Database` adapter for `SqlRuntimeStore` — and run `RuntimeStoreConformance` against it |
| A new document extractor | `integrations/document/<name>.py` — implement `DocumentExtractor` (`documents/protocols.py`) |
| A new tool | `integrations/tools/<name>/tool.py` — implement `Tool`, **declaring `risk` and `idempotent`** (auto-scanned, no registration needed) |
| A new skill | `integrations/tools/skills/<name>/SKILL.md` — YAML frontmatter + prompt body |
| A new agent flow | `agents/flows.py` — SequentialFlow / ParallelFlow / ConditionalFlow are `RoutedAgent`s |

### Tool creation

```python
from substrate.tools import ToolExecutionResult, ToolRisk
from substrate.types.content import TextBlock

class MyTool:
    name = "my_tool"
    description = "What it does"
    input_schema = {"type": "object", "properties": {...}, "required": [...]}
    risk = ToolRisk.SAFE          # required. HIGH/CRITICAL need approval.
    idempotent = True             # required. May a call that might not have happened be repeated?

    async def execute(self, *, ctx=None, **kwargs) -> ToolExecutionResult:
        return ToolExecutionResult(name=self.name, content=[TextBlock(text="result")])
```

`risk` and `idempotent` are **required** — `Toolbox.add` raises `ToolDeclarationError` without them. `idempotent`
decides what a crash window means: a non-idempotent tool whose outcome was never recorded is *not* re-run (the
run fails with `OrphanedEffectError`, the journaled intent being what you compensate from); an idempotent one
re-runs under the same idempotency key (`current_idempotency_key()`). A tool from an MCP server defaults to
`HIGH` / not idempotent unless the operator connecting it says otherwise.

A tool declares `concurrency_safe = True` when several calls to it can run at the same
time (pure reads); a turn's tool calls then run concurrently and are journaled replay-safely
(`ctx.tool_batch`). Anything that doesn't declare it runs one call at a time. A tool that
needs to know whose work it is reads `scope_of(ctx)` (tenant/user/thread/branch/agent —
`substrate.types.RunScope`), never a global and never a model-supplied argument.

`substrate.tools` re-exports the full taxonomy: `Tool` (LOCAL, `execute()`),
`HostedTool` (provider-executed, `provider_specs`), `ProviderDefinedTool`
(provider call-shape + local `handle_call()`). Use `is_hosted_tool` /
`is_provider_defined_tool` to branch at dispatch. Wire-dict encoding for each
provider lives in `integrations/llm/<provider>_client.py::_tools_from_options`
+ `integrations/llm/encoders/<provider>.py::encode_tools` — no shared core
encoder type; each provider client builds its own dicts.

Placed at `integrations/tools/my_tool/tool.py` — `CatalogScanner` discovers it automatically.

### LLM client

Every `ChatModel` exposes `capabilities: ModelCapabilities` (input modalities, context window,
prices; resolved from the model registry, or pass `capabilities=` for an unlisted local model).
It never sends content outside `capabilities.input_modalities` — unsupported media becomes a
short text note. `GenerationOptions.reasoning` (`ReasoningEffort`) is the one typed control
for reasoning/thinking; each vendor client maps it onto its own parameter.

```python
from substrate.integrations.llm import LLMFactory

# Auto-detects provider from model name prefix
client = LLMFactory("gpt-4o", api_key).build()
client = LLMFactory("groq/llama-3.3-70b-versatile", api_key).build()
client = LLMFactory("ollama/llama3.2", "ollama").build()   # local, no key

# Or construct the universal OpenAI-compatible client directly
from substrate.integrations.llm.openai_compatible import OpenAICompatibleClient
client = OpenAICompatibleClient(model="llama3.2", api_key="ollama",
                                base_url="http://localhost:11434/v1")
```

Every client reports why the model stopped (`FinishReason`) and the engine acts on it: a reply cut off at
the token limit is marked `run.truncated`, a content-filter stop fails the run as `content_filter`.
Provider failures are typed by `classify_llm_error` — `RateLimitedError` (carrying the provider's
`Retry-After`, which the worker honours), `ContextLengthError` (the agent halves the prompt and retries
once), `AuthError`, `ContentFilterError` — so a transient one retries and a permanent one does not.

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
# durable by default — the threads of the store in a folder (no server, survives a kill, safe across processes)
from substrate.stores import Store

threads = Store.at("./.substrate").threads      # usable at once; the first call opens the store
```

All `ThreadStore` methods are `async def`. Always `await` them.

### Long-term memory (`MemoryStore`)

Memory is **scope-addressed**. A record is owned by a `MemoryNamespace(tenant_id, user_id?, agent_id?, session_id?)`;
a caller passes *its own* namespace to every read and delete, and sees the records whose owner fields it shares —
so leaving a field out narrows what you see, never widens it.

```python
from substrate.stores.memory import MemoryNamespace, MemoryQuery, MemoryRecord, TenantWide

me = MemoryNamespace(tenant_id="acme", user_id="alice")
await store.save(MemoryRecord.from_text("prefers French", namespace=me))
await store.get(me, record_id)                       # an id alone addresses nothing
await store.query(MemoryQuery(namespace=me, text_query="French"))
await store.query(MemoryQuery(namespace=MemoryNamespace(tenant_id="acme"),
                              tenant_wide=TenantWide(reason="admin export, ticket 123")))  # explicit, with a reason
await store.erase(MemoryNamespace(tenant_id="acme", user_id="alice"))   # everything under a user
```

Ids are tenant-qualified (another tenant's same id is a different record); saving over another namespace's id in the
same tenant raises `ScopeViolationError`; deleting requires owning the record (same user). Implementations:
`LocalFilesystemMemoryStore` (default), `DurableMemoryStore` (Postgres, table `memory_records`), `LanceMemoryStore`;
all three run `MemoryStoreConformance` (`testing/conformance/memory_store.py`). The memory *tool* takes tenant
and user from the run's `scope_of(ctx)`, never from model arguments.
## Knowledge / RAG

Vector and graph store contracts live in the core (`stores/`). Concrete implementations live in `integrations/`; `integrations/knowledge/` wires them into pipelines.

```python
# Contracts (core)
from substrate.stores.vector import VectorStore, Document, SearchResult
from substrate.stores.graph import GraphStore, Entity, Relationship, SubGraph

# Concrete implementations
from substrate.integrations.vector import PgVectorStore
from substrate.integrations.graph import AGEGraphStore

# High-level RAG pipeline
from substrate.integrations.knowledge import RAGPipeline, GraphRAGPipeline

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

# Code interpreter sandbox: "nsjail" (default, Linux namespaces + cgroups on
# this host, no container) | "k8s" (one agent-sandbox pod per session) | "inprocess"
SANDBOX_RUNTIME=nsjail

# Agent runtime store: "postgres" (default, durable) or "local" (SQLite file, no infra).
# There is no in-memory store anywhere: the floor is a folder (or a SQLite file). Runtime tests use
# ephemeral_runtime() (substrate.testing) — the same code on a store in a throwaway folder.
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
| Monolith backend | 8000 | `uv run substrate start` |
| Tempo | 4318 | OTLP HTTP |
| Grafana | 3001 | Dashboard |

---

## Observability Stack

All observability services start via `make infra-up`.

| Component | Purpose |
|---|---|
| Loki + Promtail | Log aggregation — scrapes pod/service stdout |
| Tempo | Distributed tracing (OTLP → `localhost:4318`) |
| Prometheus | Metrics collection |
| Grafana | Dashboards at `http://localhost:3001` (admin/admin) |

Logging convention:
```python
# application code (serving/, integrations/, console/, cli) — configures handlers on first use
from substrate.logger import setup_logging
logger = setup_logging("substrate.my_module")

# the core is a library: it only emits, and the host decides where it goes
import logging
logger = logging.getLogger(__name__)
```
The core must not import `substrate.logger` — importing the engine configures nothing and loads no logging stack
(row I26 loads the whole engine in a subprocess and checks what came in).

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
from substrate.evals import EvalCase, EvalDataset, LLMJudge, EvalRunner, CORRECTNESS

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
- New skills → `src/substrate/integrations/tools/skills/<name>/SKILL.md` with YAML frontmatter
- **DB session dependency** — all microservice routes use `get_db_session` from `serving/shared/database/`. Never define a local `_get_db` helper.
- **Testing** — `asyncio_mode = "auto"` in `pyproject.toml`: write `async def test_*` directly, no `@pytest.mark.asyncio` needed.
- **Interactive Console** — `/q` is the sole quit/exit command. Interactive session uses `prompt_toolkit` for async-compatible autocomplete of slash commands (`/tools`, `/skills`, `/reset`, `/help`, `/q`) and input history.

---

## Known Tech Debt

| Area | Issue |
|---|---|
| Test coverage | `guardrails`/`middleware`/MCP adapter/`evals` have real (if not exhaustive) coverage as of 2026-07-05 — the genuinely thin area is **microservices business logic** (`identity`, `policy`, `job_controller`, `tool_executor`, `code_interpreter`): only health/smoke tests exist (`tests/server/test_services_health.py`), no per-service behavior tests. See `docs/claude_docs/roadmap.md` "Recently shipped" (v1 remediation) for what else shipped that session and its known gaps. |
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
