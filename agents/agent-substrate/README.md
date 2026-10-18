<center><h1>Agent Substrate</h1></center>

**A production-ready, protocol-oriented Python framework for building robust, observable, and composable autonomous AI agents and durable multi-agent workflows.**

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docs](https://img.shields.io/badge/docs-docs.agent--substrate.com-teal)](https://docs.agent-substrate.com)

<p align="center">
  <img src="docs/assets/demo.gif" alt="The chat app built on Agent Substrate: streaming answers, scheduled tasks, approvals, notifications and settings" width="860" />
  <br /><sub>The chat app that ships with it (<code>substrate-ui</code>) running on this engine.</sub>
</p>

---

## 🚀 Features

*   **🤖 ReAct & Multi-Agent Loops**: Production-grade Reasoning + Action loop (`ReActAgent`), hub-and-spoke sub-agent delegation (`OrchestratorAgent`), human-in-the-loop gates (`DurableApproval`), supervision token budgets (`SpawnBudget`, `ExecutionBudget`), and priority preemption.
*   **🔧 Type-Safe Tool System**: `@tool(risk=ToolRisk.SAFE, idempotent=True)` decorator with automated schema reflection, risk-tiered approval gating (`ToolRisk`), MCP client integration, and sandboxed code execution.
*   **💾 Unified Storage (`substrate.stores`)**: Single `Store` port (`connect("./.substrate")` or `postgres_store`) managing:
    *   **Threads**: DAG-based conversation history with branching, forking, and compaction checkpoints.
    *   **Vectors**: Dense similarity, lexical word matching, and hybrid RRF fusion (`SearchableVectorStore`).
    *   **Memory**: Key-value session state (short-term) and semantic fact extraction (long-term).
    *   **Tasks**: Per-agent Kanban boards with 6-state lifecycle tracking.
    *   **Knowledge Graph**: Entity/relationship graph store with Cypher query capability.
    *   **Files**: Content-addressed blob and workspace file store with quota management.
*   **📄 Layout-Aware Document Intelligence (`substrate.documents`)**: `Reader` parses PDF, DOCX, PPTX, XLSX, ODF, HTML, Markdown, and CSV into structured markdown. Files into an OKF `Library` where models navigate documents using `DocumentsTool` (outline, read, find, view) without context dumping.
*   **🛡️ 13 Built-in Middlewares & Safety**: Turn-key guardrails covering PII detection, prompt-injection defense with UTS-39 homoglyph normalization, token limiters, caching, schema validation, rate limiters, and LLM-as-a-judge.
*   **📡 Deterministic Durability & Replay**: Hierarchical effect journaling (`Effect.make_id`) guaranteeing at-most-once side-effects on replay, lease-based worker scheduling, and verified crash recovery.
*   **🎯 Multi-Provider LLMs**: Native support for OpenAI, Anthropic, Gemini, Groq, and Ollama via `LLMFactory` and `create_model_client`.
*   **🕷️ Composable Flows**: Pure coordination primitives (`SequentialFlow`, `ParallelFlow`, `ConditionalFlow`) for deterministic agent pipelines.
*   **📊 Zero-Overhead Observability**: Built-in OpenTelemetry instrumentation (`opentelemetry-api` with zero runtime overhead), structured logging, and Grafana / Tempo integration.
*   **🌐 HTTP & SSE Serving (`substrate.server`)**: Turn-key FastAPI integration (`create_app`) with AG-UI wire protocol and Server-Sent Events (SSE).

---

## 📋 Table of Contents

*   [Quick Start](#-quick-start)
*   [Tech Stack: required vs opt-in](#-tech-stack-required-vs-opt-in)
*   [Core Architecture](#-core-architecture)
*   [Key Patterns](#-key-patterns)
*   [Multi-Agent Workflows](#-multi-agent-workflows)
*   [Storage & Persistence](#-storage--persistence)
*   [Document Intelligence](#-document-intelligence)
*   [Installation & Setup](#-installation--setup)
*   [Testing](#-testing)
*   [Documentation](#-documentation)

---

## ⚡ Quick Start

### Installation

Add it to your project using `uv` (recommended) or `pip`:

```bash
uv add agent-substrate
# or
pip install agent-substrate
```

The base install contains the complete engine (`pydantic`, `typing_extensions`, `opentelemetry-api`, `pypdfium2`, and `confusable_homoglyphs`) and stores everything in an embedded SQLite folder, so it runs with no other service. Optional integrations (vendor LLMs, Postgres, Redis, S3, OCR) are extras; see [Tech Stack](#-tech-stack-required-vs-opt-in):

```bash
uv add "agent-substrate[openai,postgres]"
```

### Your First Agent

`Runtime.run(agent, prompt)` is the one-shot entry point: it registers the agent, executes the prompt, and returns a `RunOutcome` with the final answer.

```python
import asyncio
from substrate import ReActAgent, Runtime
from substrate.integrations.llm import LLMFactory

async def main():
    llm = LLMFactory("gpt-4o", api_key="sk-...").build()

    agent = ReActAgent(
        "assistant",
        model=llm,
        system_instructions="You are a helpful assistant.",
    )

    async with Runtime.open("./.substrate") as runtime:
        result = await runtime.run(agent, "Write a Python function to compute Fibonacci numbers.")
        print(result.output)

if __name__ == "__main__":
    asyncio.run(main())
```

### Agent with Tools

You can define tools using the `@tool` decorator or reusable tool classes:

```python
import asyncio
from substrate import ReActAgent, Runtime, ToolRisk, tool
from substrate.integrations.llm import LLMFactory
from substrate.integrations.tools.compute.calculator import CalculatorTool

@tool(risk=ToolRisk.SAFE, idempotent=True)
def shout(text: str) -> str:
    """Upper-case the text."""
    return text.upper()

async def main():
    llm = LLMFactory("gpt-4o", api_key="sk-...").build()

    agent = ReActAgent(
        "math_expert",
        model=llm,
        tools=[shout, CalculatorTool()],
        system_instructions="Answer math queries using the calculator.",
    )

    async with Runtime.open("./.substrate") as runtime:
        result = await runtime.run(agent, "Calculate 1234 * 5678 and shout the result.")
        print(result.output)

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🧱 Tech Stack: required vs opt-in

Two things are called "Agent Substrate": the **library** (`pip install agent-substrate`, an engine you embed) and the **platform** (`apps/substrate-cloud`, a multi-tenant HTTP server built on it, which the chat app talks to). They need different things.

### The library needs nothing but Python

| | |
|---|---|
| **Required** | Python 3.13+, `pydantic`, `typing_extensions`, `opentelemetry-api`, `pypdfium2`, `confusable_homoglyphs`. |
| **State** | One **embedded SQLite** database plus a files folder (`Runtime.open("./.substrate")`): runs and their journal, threads, memory, tasks, graph, vectors, files. **No Postgres, no Redis, no vector database** to install. |
| **Documents** | PDF through PDFium, and DOCX/PPTX/XLSX/ODF/HTML/Markdown/CSV with the standard library, all in the base install. Scanned pages: the `tesseract` program if it is on your `PATH`, otherwise they are reported in `needs_ocr`, never returned silently empty. |
| **Search** | Words (SQLite full-text) out of the box. Meaning (embeddings) only when you give a `Library` an embedder. |

Everything below is an **extra you choose** (`uv add "agent-substrate[openai,postgres]"`); nothing imports until you use it.

| Opt in when you want… | Extra / service | Adds |
|---|---|---|
| A model provider | `openai` (also vLLM, llama.cpp, Ollama, LM Studio), `anthropic`, `gemini` | that vendor's SDK |
| **PostgreSQL** instead of SQLite (several workers or hosts) | `postgres` | `asyncpg`; vectors use the **pgvector** extension |
| Redis (session cache, event bus) | `redis` | `redis` client |
| Object storage instead of local disk | `s3` | `aiobotocore` (works with SeaweedFS, MinIO, S3) |
| Scheduled / cron tasks | `scheduler` | `apscheduler` |
| OCR for scanned pages without a system program | `ocr` | **RapidOCR**: PaddleOCR's own small models on ONNX Runtime, inside the wheel (~120-150 MB) |
| Layout, tables and chart reading | the **document-intelligence service** (`make infra-up-document-intelligence`) | **PaddleOCR-VL** in a CPU container (~1 GB image), called over HTTP |
| Search by meaning | an embedding model, or the **embedding-reranker service** (`make infra-up-embedding-reranker`) | Qwen3-VL embedding and reranking |
| Prompt-injection / NSFW guardrails | `safety` | ONNX classifiers (`onnxruntime`, `tokenizers`) |
| Web search and browsing | `web` | Playwright, crawl4ai, Tavily/Exa clients (hundreds of MB) |
| Code execution | `sandbox` (data-science packages) and an `nsjail` or Kubernetes (`code`) runtime | |
| MCP tools, TTS, the CLI console | `mcp`, `tts` (Kokoro), `console` | |
| An HTTP/SSE server around an agent | `serve` | `fastapi` |

Full reasoning per extra (sizes, pins, exclusivity) is in [`optional-dependencies.md`](docs/claude_docs/architecture/optional-dependencies.md).

> **No LanceDB.** Vectors live in the same database as everything else (SQLite, or PostgreSQL with pgvector). The "Lance database" comment in `stores/store.py` is only an analogy for how the folder is laid out.

### The platform (`apps/substrate-cloud`) is a deployment, so it needs services

| Needed | Why |
|---|---|
| **PostgreSQL 18** (with `pgvector`) | The application tables (users, threads, files, scheduled tasks, notifications, preferences) use JSONB and row-level security, and the engine store runs on it (`STORE_BACKEND=postgres`). `STORE_BACKEND=local` keeps the engine state in a folder, but the application tables still need Postgres. |
| **Redis 7** | Rate limits and daily quotas, the session cache, the event bus. |
| An LLM key | Any one provider above. |
| **Docker** | Only to run the two services above with `make infra-up`. |

Optional on the platform, each off until configured: SeaweedFS or S3 for files (`FILE_STORE_BACKEND`), the document-intelligence and embedding-reranker services, ONLYOFFICE for editing Office files server-side, Resend for emailing scheduled-task results (`RESEND_API_KEY`), and Grafana, Loki and Tempo for observability.

### The apps around it

| App | Stack |
|---|---|
| `substrate-ui` (chat) | Next.js 16, React, TypeScript, Tailwind v4, Radix UI, pnpm. |
| `agent-substrate-platform` (control plane) | Next.js 16, Prisma on PostgreSQL, Auth.js, Tailwind v4, pnpm. |

---

## 🏛️ Core Architecture

Agent Substrate is organized into **13 strictly ordered concept layers** (`tests/_layout.py`). Imports flow strictly downward — a concept can only import from layers below it:

```
agents         ← ReActAgent, OrchestratorAgent, UserProxyAgent, Flows (Sequential, Parallel, Conditional)
  runtime        ← Runtime, Worker, Scheduler, Journal, Commit, Effect log
    middleware   ← 13 built-in middlewares (PII, Prompt Injection, Cache, Retry, Truncation, LLM Judge)
      context      ← Context window management, sliding-window compaction, token budgeting
        safety       ← Input safety classifiers, UTS-39 homoglyph normalization
          workspace    ← Content-addressed workspace filesystems, branching, snapshots
            documents    ← Layout-aware Reader, OKF Library, DocumentsTool
              stores       ← Store facade, SQLite WAL database, Threads, Vectors, Memory, Tasks, Graphs
                models       ← ChatModel / EmbeddingModel protocols, capabilities registry, error classification
                  tools        ← @tool decorator, Tool protocol, approval gates, chains, skills
                    telemetry    ← OpenTelemetry spans and metrics (API only, zero runtime overhead)
                      types        ← ContentBlock union, Message envelope, Actor/Topic IDs, Errors, Usage
```

**Outside the Core:**
*   **`substrate.integrations`**: Out-of-tree adapters for third-party vendors (OpenAI, Anthropic, Gemini, Groq, Ollama), database drivers (`PostgresDatabase`, `postgres_store`), object stores (`S3FileStore`), Redis, and sandboxes (Docker / nsjail).
*   **`substrate.server`**: Turn-key FastAPI HTTP and Server-Sent Events (SSE) serving layer with AG-UI wire protocol support.
*   **`tests/invariants`**: An executable invariant register with 221 property tests verifying crash survival, replay determinism, and zero vendor leak.

---

## 🔑 Key Patterns

### Writing Tools

#### Function Decorator
```python
from substrate import tool, ToolRisk

@tool(risk=ToolRisk.SAFE, idempotent=True)
def get_user(user_id: str) -> dict:
    """Look up a user account by ID."""
    return {"id": user_id, "name": "Alice"}
```

#### Class-Based Tool
```python
from substrate.tools import ToolExecutionResult
from substrate.types.content import TextBlock

class CustomTool:
    name = "custom_tool"
    description = "Perform a custom operation."
    input_schema = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    async def execute(self, *, ctx=None, **kwargs) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text="Success")])
```

### Human-in-the-Loop (HITL) Approvals

Risky tools pause execution durably without blocking the worker process:

```python
from substrate.tools import DurableApproval, ApprovalDecision, tool, ToolRisk

approval = DurableApproval()

@tool(risk=ToolRisk.HIGH)
async def transfer_funds(account: str, amount: float) -> str:
    """Transfer funds to another account."""
    return f"Transferred ${amount} to {account}"

# In your runtime or HTTP handler, resolve pending decisions:
# await approval.resolve(request_id, ApprovalDecision.APPROVE)
```

---

## 💾 Storage & Persistence

The entire state of an agent (threads, memory, vector embeddings, tasks, and files) is encapsulated in a single `Store`:

```python
from substrate import Store, connect

# Local embedded database (SQLite WAL + files directory):
store = await connect("./.substrate")

# Scale out across multiple worker processes / hosts:
from substrate.integrations.database import postgres_store
store = await postgres_store("postgresql://user:pass@localhost:5432/agentdb")
```

### Store Facets
*   `store.threads`: DAG-based conversation threads with branching, forking, and history checkpoints.
*   `store.vectors`: Multimodal vector storage supporting exact, lexical, and hybrid Reciprocal Rank Fusion (RRF) search.
*   `store.memory`: Long-term episodic memory with full-text search + short-term session state.
*   `store.tasks`: Scoped Kanban task boards with automatic retry tracking.
*   `store.graph`: Knowledge graph entities and relationships with Cypher query support.
*   `store.files`: Content-addressed file store with per-tenant quotas.

---

## 📄 Document Intelligence

Read, file, and navigate documents without flooding the LLM context window:

```python
from substrate.documents import Reader, Library, DocumentsTool
from substrate.stores import Store

# Read PDF, DOCX, PPTX, XLSX, ODF, HTML, Markdown, CSV:
result = await Reader().read(data, "report.pdf")

store = Store.at("./.substrate")
library = Library(store)
await library.add(data, "report.pdf", collection="conversations/c1/documents")

# Give the agent a tool to navigate the library (list, outline, read, find, view):
doc_tool = DocumentsTool(library, collection=lambda scope: "conversations/c1/documents")
```

---

## 🕸️ Multi-Agent Workflows

### OrchestratorAgent — Hub & Spoke

`OrchestratorAgent` coordinates specialist sub-agents using dynamic tool delegation:

```python
from substrate import OrchestratorAgent, SubAgentConfig, ReActAgent, Runtime

researcher = ReActAgent("researcher", model=llm, system_instructions="Research topics.")
writer = ReActAgent("writer", model=llm, system_instructions="Draft articles.")

orchestrator = OrchestratorAgent(
    "coordinator",
    model=llm,
    sub_agents=[
        SubAgentConfig(agent=researcher, description="Research technical topics"),
        SubAgentConfig(agent=writer, description="Write clear blog posts"),
    ],
)

async with Runtime.open("./.substrate") as runtime:
    result = await runtime.run(orchestrator, "Research and draft an article on Rust vs Go.")
    print(result.output)
```

### Deterministic Flows

Compose pipelines using `SequentialFlow`, `ParallelFlow`, and `ConditionalFlow`:

```python
from substrate.agents import SequentialFlow
from substrate.runtime import Runtime
from substrate.types import Actor

class FetchStep:
    id = Actor(type="agent", key="fetch")
    async def run(self, ctx, inbox):
        for msg in inbox:
            await ctx.reply(msg, {"text": "Fetched 3 records."})

class AnalyzeStep:
    id = Actor(type="agent", key="analyze")
    async def run(self, ctx, inbox):
        for msg in inbox:
            await ctx.reply(msg, {"text": "Analysis: all records valid."})

async def main():
    fetch, analyze = FetchStep(), AnalyzeStep()
    pipeline = SequentialFlow(steps=[fetch, analyze], name="demo_pipeline")

    async with Runtime.open("./.substrate") as runtime:
        await runtime.register(fetch)
        await runtime.register(analyze)
        result = await runtime.ask(pipeline, "Process the latest dataset.")
        print(result.output)
```

---

## 🧪 Testing

```bash
# Run unit & integration test suites
uv run pytest

# Run the 221-test Invariant Register (durability, replay determinism, import boundaries)
uv run pytest tests/invariants/

# Run README and example suites
uv run pytest tests/test_readme_examples.py tests/test_examples.py

# Check import boundaries and concept layering
uv run lint-imports
```

---

## 📖 Documentation

*   [Architecture Deep Dive](docs/claude_docs/architecture/kernel.md) — Concept hierarchy, runtime stores, and design rationale.
*   [The Invariant Register](docs/claude_docs/architecture/invariants.md) — 221 executable guarantees on durability, safety, and tenancy.
*   [Document Intelligence](docs/claude_docs/architecture/documents.md) — Multi-format reading, OKF filing, and knowledge base search.
*   [Human in the Loop](docs/claude_docs/architecture/hitl.md) — Durable approval gates and worker pause/resume.
*   [Multi-Tenant Isolation](docs/claude_docs/architecture/tenant-isolation-rls.md) — Tenancy scopes and data fencing.

---

**Built with ❤️ for the AI agent engineering community**
