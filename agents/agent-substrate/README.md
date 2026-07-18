<center><h1>Agent Substrate</h1></center>

**A production-ready, protocol-oriented Python framework for building robust, observable, and composable autonomous AI agents and multi-agent workflows.**

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docs](https://img.shields.io/badge/docs-docs.agent-substrate.com-teal)](https://docs.agent-substrate.com)

---

## 🚀 Features

*   **🤖 ReAct Agent Loop**: Production-grade Reasoning + Action loop with HITL gates, supervision budgets, and priority preemption.
*   **🔧 Safe Tool Execution**: JSON-schema-validated tools, risk-tiered approval gating, sandboxed code-mode chaining, and MCP integration.
*   **💾 Pluggable Memory**: `CachedHistoryProvider` (fast Redis cache, self-heals from durable storage on a cold session) is the production default; in-memory, Redis-only, and Postgres providers are also available standalone. Sliding-window, token-budget, and summarization compaction strategies included.
*   **🎯 Multi-Provider LLM**: OpenAI, Anthropic, Gemini, Groq, Ollama — auto-detected from model name prefix via `LLMFactory`.
*   **📊 Guardrails & Middleware**: Async tripwire pipeline evaluating inputs, outputs, and tool calls with mutation policies.
*   **🕷️ Composable Flows**: `SequentialFlow`, `ParallelFlow`, and `ConditionalFlow` nest recursively in `fabric/`.
*   **📡 Durable Execution**: Postgres-backed event log + inbox + scheduler for at-most-once delivery and crash recovery.
*   **📊 Observability**: OpenTelemetry traces, structured logging, lifecycle hooks, and a Grafana dashboard out of the box.

---

## 📋 Table of Contents

*   [Quick Start](#-quick-start)
*   [Core Architecture](#-core-architecture)
*   [Key Patterns](#-key-patterns)
*   [Multi-Agent Workflows](#-multi-agent-workflows)
*   [Installation & Setup](#-installation--setup)
*   [Testing](#-testing)
*   [Documentation](#-documentation)

---

## ⚡ Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/Ravikumarchavva/agent-substrate
cd agent-substrate

# Sync dependencies
uv sync

# Start infrastructure (Postgres, Redis, MinIO, observability)
make infra-up
```

### Your First Agent

`Runtime.run(agent, prompt)` is the one-shot entry point: it registers the
agent, submits the prompt, and returns a `RunOutcome` once the agent's final
answer is available.

```python
import asyncio
from substrate.agents import ReActAgent, Runtime
from substrate.integrations.llm import LLMFactory

async def main():
    llm = LLMFactory("gpt-4o", api_key="sk-...").build()

    agent = ReActAgent(
        "assistant",
        model=llm,
        system_instructions="You are a helpful assistant.",
    )

    async with Runtime() as runtime:
        result = await runtime.run(agent, "Write a Python function to compute Fibonacci numbers.")
        print(result.output)

if __name__ == "__main__":
    asyncio.run(main())
```

### Agent with Tools

```python
import asyncio
from substrate.agents import ReActAgent, Runtime
from substrate.capabilities.tools.compute.calculator import CalculatorTool
from substrate.integrations.llm import LLMFactory

async def main():
    llm = LLMFactory("gpt-4o", api_key="sk-...").build()

    agent = ReActAgent(
        "math_expert",
        model=llm,
        tools=[CalculatorTool()],
        system_instructions="Always use the calculator tool to solve math problems.",
    )

    async with Runtime() as runtime:
        result = await runtime.run(agent, "Calculate 1234 * 5678.")
        print(result.output)

if __name__ == "__main__":
    asyncio.run(main())
```

`CalculatorTool` evaluates arithmetic via a whitelisted AST walk — no
`eval()` — so LLM-controlled input can never reach arbitrary code. Writing
your own tool that evaluates expressions? Reuse `substrate.capabilities.
tools.compute.calculator.safe_eval` rather than calling `eval()` yourself.

---

## 🏛️ Core Architecture

Agent Substrate is partitioned into **four strict dependency layers**. Imports flow strictly downward — lower layers never depend on higher ones:

```
fabric (L3)        ← Flows (Sequential/Parallel/Conditional), Evals, durable execution
  capabilities (L2)  ← Tools, Skills, Knowledge/RAG, Memory, Vector/Graph stores, Triggers
    agents (L1)      ← ReActAgent, OrchestratorAgent, Runtime, Middleware, Guardrails
      kernel (L0)    ← FROZEN. Protocols, ContentBlock types, AgentId, Tool contracts
```

**Orthogonal layers** (implement kernel Protocols, cross-cut all layers):

| Layer | Responsibility |
|---|---|
| `integrations/` | Third-party adapters: LLM providers, MCP, event bus, connectors |
| `infrastructure/` | Engine backends: Postgres, Redis, MinIO, durable runtime |
| `serving/` | Deployment shells: monolith FastAPI app + 12 microservices |

Import-linter enforces the layer contract on every CI run (`uv run lint-imports`).

**[→ Capability Map](docs/capability-map.md)** — the platform organized by concern: context (the RAM tier), memory (short-term + long-term with pluggable backends), storage, guardrails, governance, evals, observability, and tools. Every item names a real, shipped class.

**[→ Kernel Board](docs/kernel-board.html)** — the contract-level view: every kernel protocol drawn as a socket, traced to the real implementations that plug into it.

**[→ Agent Builder](docs/agent-builder.html)** — pick a memory backend, tools, guardrails, and budgets; get real, accurate `ReActAgent` construction code back, generated from the actual constructor signatures.

---

## 🔑 Key Patterns

### Adding a Tool

Drop a file at `src/substrate/capabilities/tools/<name>/tool.py` — `CatalogScanner` discovers it automatically, no registration needed:

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

### LLM Client

```python
from substrate.integrations.llm import LLMFactory

# Provider auto-detected from model name prefix
client = LLMFactory("gpt-4o", api_key).build()
client = LLMFactory("claude-opus-4-8", api_key).build()
client = LLMFactory("groq/llama-3.3-70b-versatile", api_key).build()
client = LLMFactory("ollama/llama3.2", "ollama").build()   # local, no key
```

### MCP Tools

```python
from substrate.integrations.tools.mcp import MCPClient, MCPTool

client = MCPClient(url="http://localhost:9000/sse")
tools = await MCPTool.from_mcp_client(client)   # list[MCPTool]
```

### Knowledge / RAG

```python
from substrate.capabilities.vector import PgVectorStore
from substrate.capabilities.knowledge import RAGPipeline

pipeline = RAGPipeline(embedding_client=embed_client, vector_store=PgVectorStore(...))
await pipeline.ingest("Long document …", collection="kb")
results = await pipeline.query("What is X?", collection="kb")
```

---

## 🕸️ Multi-Agent Workflows

### OrchestratorAgent — Hub & Spoke

`OrchestratorAgent` delegates to sub-agents via an LLM-driven tool call, and
also works with `Runtime.run()` — its final synthesized answer streams
through the same mechanism as `ReActAgent`'s.

```python
from substrate.agents import OrchestratorAgent, SubAgentConfig, ReActAgent

researcher = ReActAgent("researcher", model=llm, system_instructions="Research the web.")
writer = ReActAgent("writer", model=llm, system_instructions="Write content.")

orchestrator = OrchestratorAgent(
    "coordinator",
    model=llm,
    sub_agents=[
        SubAgentConfig(agent=researcher, description="Web research"),
        SubAgentConfig(agent=writer, description="Content writing"),
    ],
)

async with Runtime() as runtime:
    result = await runtime.run(orchestrator, "Research and draft a blog post about Rust vs Go.")
    print(result.output)
```

### Flows — Coordination Primitives

`SequentialFlow`, `ParallelFlow`, and `ConditionalFlow` (in `fabric/flows/`)
are `Agent`-shaped coordinators: instead of streaming text via an LLM call,
they reply to their caller via `ctx.reply()` — the same mechanism any agent
uses to answer an `ctx.ask()`. Use **`Runtime.ask()`**, not `Runtime.run()`,
to invoke one directly and read its result — register each step with the
`Runtime` first:

```python
from substrate.agents.runtime import Runtime
from substrate.fabric.flows import SequentialFlow
from substrate.kernel.core.identity import AgentId

class FetchStep:
    id = AgentId(type="step", key="fetch")
    async def run(self, ctx, inbox):
        for msg in inbox:
            await ctx.reply(msg, {"text": "Fetched 3 records."})

class AnalyzeStep:
    id = AgentId(type="step", key="analyze")
    async def run(self, ctx, inbox):
        for msg in inbox:
            await ctx.reply(msg, {"text": "Analysis: all records valid."})

async def main():
    fetch, analyze = FetchStep(), AnalyzeStep()
    pipeline = SequentialFlow(steps=[fetch, analyze], name="demo_pipeline")

    async with Runtime() as runtime:
        await runtime.register(fetch)
        await runtime.register(analyze)
        result = await runtime.ask(pipeline, "Process the latest dataset.")
        print(result.output)
        # Process the latest dataset.
        #
        # Fetched 3 records.
        #
        # Analysis: all records valid.
```

`SequentialFlow`'s reply is the **full accumulated trace** (input + every
step's output, joined by blank lines) — not just the last step's output.
`ParallelFlow` (`branches=[...]`, `merge="concat"|"vote"|callable`) and
`ConditionalFlow` (`predicate`, `if_true`, `if_false`) follow the same
`Runtime.ask()` pattern.

---

## 🔧 Installation & Setup

### Environment Variables (`.env`)

```bash
# LLM providers (set at least one)
OPENAI_API_KEY=sk-proj-...
ANTHROPIC_API_KEY=sk-ant-...

# Database
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agentdb

# Redis
REDIS_URL=redis://localhost:6379/0

# Auth (required)
JWT_SECRET=<32+ char random string>

# Observability
OTLP_ENDPOINT=http://localhost:4318
```

The monolith server (`uv run start` / `substrate start`) listens on port
**8000** by default.

---

## 🧪 Testing

```bash
# Run full test suite
uv run pytest

# Single file
uv run pytest tests/test_foo.py

# Architecture + import-linter checks
uv run lint-imports

# Full CI preflight (lint → typecheck → test → security)
make ci
```

---

## 📖 Documentation

Full architecture reference, layer guides, and API docs at **[agent-substrate.pages.dev](https://docs.agent-substrate.com)**.

---

**Built with ❤️ for the AI agent engineering community**
