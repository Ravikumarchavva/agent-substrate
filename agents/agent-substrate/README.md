<center><h1>Agent Substrate</h1></center>

**A production-ready, protocol-oriented Python framework for building robust, observable, and composable autonomous AI agents and multi-agent workflows.**

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docs](https://img.shields.io/badge/docs-agent--substrate.pages.dev-teal)](https://agent-substrate.pages.dev)

---

## 🚀 Features

*   **🤖 ReAct Agent Loop**: Production-grade Reasoning + Action loop with HITL gates, supervision budgets, and priority preemption.
*   **🔧 Safe Tool Execution**: JSON-schema-validated tools, risk-tiered approval gating, sandboxed code-mode chaining, and MCP integration.
*   **💾 Pluggable Memory**: In-memory, Redis, and Postgres history providers with sliding-window, token-budget, and summarization compaction strategies.
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

```python
import asyncio
from agent_substrate.agents import ReActAgent, Runtime
from agent_substrate.agents.context import ContextConfig, InMemoryHistoryProvider
from agent_substrate.integrations.llm import LLMFactory

async def main():
    runtime = Runtime()
    await runtime.start()

    llm = LLMFactory("gpt-4o", api_key="...").build()

    agent = ReActAgent(
        id="assistant",
        llm=llm,
        system="You are a helpful assistant.",
        context_config=ContextConfig(history=InMemoryHistoryProvider()),
    )

    result = await runtime.run(agent, "Write a Python function to compute Fibonacci numbers.")
    print(result.output)

    await runtime.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### Agent with Tools

```python
import asyncio
from agent_substrate.agents import ReActAgent, Runtime
from agent_substrate.integrations.llm import LLMFactory
from agent_substrate.kernel.tools import ToolExecutionResult
from agent_substrate.kernel.core.content import TextBlock

class CalculatorTool:
    name = "calculator"
    description = "Evaluates a mathematical expression."
    input_schema = {
        "type": "object",
        "properties": {"expression": {"type": "string"}},
        "required": ["expression"],
    }

    async def execute(self, *, ctx=None, expression: str) -> ToolExecutionResult:
        try:
            result = eval(expression, {"__builtins__": {}}, {})
            return ToolExecutionResult(content=[TextBlock(text=str(result))])
        except Exception as e:
            return ToolExecutionResult(content=[TextBlock(text=f"Error: {e}")], is_error=True)

async def main():
    runtime = Runtime()
    await runtime.start()

    llm = LLMFactory("gpt-4o", api_key="...").build()
    agent = ReActAgent(
        id="math_expert",
        llm=llm,
        tools=[CalculatorTool()],
        system="Always use the calculator tool to solve math problems.",
    )

    result = await runtime.run(agent, "Calculate 1234 * 5678.")
    print(result.output)
    await runtime.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

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

---

## 🔑 Key Patterns

### Adding a Tool

Drop a file at `src/agent_substrate/capabilities/tools/<name>/tool.py` — `CatalogScanner` discovers it automatically, no registration needed:

```python
from agent_substrate.kernel.tools import ToolExecutionResult
from agent_substrate.kernel.core.content import TextBlock

class MyTool:
    name = "my_tool"
    description = "What it does"
    input_schema = {"type": "object", "properties": {...}, "required": [...]}

    async def execute(self, *, ctx=None, **kwargs) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text="result")])
```

### LLM Client

```python
from agent_substrate.integrations.llm import LLMFactory

# Provider auto-detected from model name prefix
client = LLMFactory("gpt-4o", api_key).build()
client = LLMFactory("claude-opus-4-8", api_key).build()
client = LLMFactory("groq/llama-3.3-70b-versatile", api_key).build()
client = LLMFactory("ollama/llama3.2", "ollama").build()   # local, no key
```

### MCP Tools

```python
from agent_substrate.integrations.tools.mcp import MCPClient, MCPTool

client = MCPClient(url="http://localhost:9000/sse")
tools = await MCPTool.from_mcp_client(client)   # list[MCPTool]
```

### Knowledge / RAG

```python
from agent_substrate.capabilities.vector import PgVectorStore
from agent_substrate.capabilities.knowledge import RAGPipeline

pipeline = RAGPipeline(embedding_client=embed_client, vector_store=PgVectorStore(...))
await pipeline.ingest("Long document …", collection="kb")
results = await pipeline.query("What is X?", collection="kb")
```

---

## 🕸️ Multi-Agent Workflows

### OrchestratorAgent — Hub & Spoke

```python
from agent_substrate.agents import OrchestratorAgent, SubAgentConfig

orchestrator = OrchestratorAgent(
    id="coordinator",
    llm=llm,
    sub_agents=[
        SubAgentConfig(agent=researcher, description="Web research"),
        SubAgentConfig(agent=writer, description="Content writing"),
    ],
)
```

### SequentialFlow — Linear Pipeline

```python
from agent_substrate.fabric.flows import SequentialFlow

pipeline = SequentialFlow(steps=[fetcher_agent, parser_agent, formatter_agent])
result = await runtime.run(pipeline, "Process this document.")
```

### ParallelFlow — Concurrent Execution

```python
from agent_substrate.fabric.flows import ParallelFlow

evaluator = ParallelFlow(
    branches=[security_auditor, legal_checker, grammar_advisor],
    merge="concat",
)
```

### ConditionalFlow — Dynamic Routing

```python
from agent_substrate.fabric.flows import ConditionalFlow

router = ConditionalFlow(
    predicate=lambda text: "bug" in text.lower(),
    if_true=bug_tracker_agent,
    if_false=general_inbox_agent,
)
```

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

Full architecture reference, layer guides, and API docs at **[agent-substrate.pages.dev](https://agent-substrate.pages.dev)**.

---

**Built with ❤️ for the AI agent engineering community**
