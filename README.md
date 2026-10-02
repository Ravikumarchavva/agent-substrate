<center><h1>Agent Substrate</h1></center>

**A production-ready, protocol-oriented Python framework for building robust, observable, and composable autonomous AI agents and durable multi-agent workflows.**

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docs](https://img.shields.io/badge/docs-docs.agent--substrate.com-teal)](https://docs.agent-substrate.com)

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

The base install contains the complete engine (`pydantic`, `typing_extensions`, `opentelemetry-api`, `pypdfium2`, and `confusable_homoglyphs`). Optional integrations (vendor LLMs, Postgres, Redis, S3, OCR) can be installed as extras:

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
