<center><h1>Ravi Agent Framework</h1></center>

**A production-ready, protocol-oriented Python framework for building robust, observable, and composable autonomous AI agents and multi-agent workflows.**

[![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## 🚀 Features

*   **🤖 Protocol-Oriented Actor Mesh**: Uniform Erlang-style message passing (`send`/`publish`) using `AgentId` routing coordinates.
*   **🔧 Safe Tool Execution**: Pydantic schema validation, granular timeout handlers, human-in-the-loop (HITL) gates, and Saga compensation transactions.
*   **💾 Context & Memory Management**: Advanced prompt-compaction strategies (sliding windows, token budgetary constraints, dynamic summarizers) to fit LLM context ceilings.
*   **🎯 Multi-Provider Support**: Seamless support for OpenAI, Anthropic, Gemini, Ollama, and more.
*   **📊 Enterprise-Grade Guardrails**: Real-time evaluation of inputs, outputs, and tool calls in parallel using async tripwires and mutation policies.
*   **🕷️ Composable Flows**: Linear, parallel, and conditional multi-agent pipelines that nest recursively.
*   **📊 Observability**: Fully integrated lifecycle hooks, structured logging, and OTel spans for tracing.

---

## 📋 Table of Contents

*   [Quick Start](#-quick-start)
*   [Core Architecture (L0-L5)](#-core-architecture-l0-l5)
*   [Core Primitives & Concepts](#-core-primitives--concepts)
*   [Multi-Agent Workflows](#-multi-agent-workflows)
*   [Installation & Setup](#-installation--setup)
*   [Testing](#-testing)
*   [Roadmap](#-roadmap)

---

## ⚡ Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/Ravikumarchavva/agent_substrategit
cd ravi/ravi-engine

# Sync dependencies using uv
uv sync

# Optional: install notebooks, browser automation, or S3 support
uv sync --group notebooks --group browser --group storage
```

### Your First Assistant Agent

Ravi utilizes `AssistantAgent` to execute the **ReAct (Reasoning and Action)** loop inside an asynchronous `LocalRuntime`:

```python
import asyncio
from agent_substrate.integrations.llm.openai.openai_client import OpenAIClient
from agent_substrate.fabric.runtime.local import LocalRuntime
from agent_substratereasoning.agents.assistant.agent import AssistantAgent

async def main():
    # 1. Initialize and start the Local Runtime fabric
    runtime = LocalRuntime()
    await runtime.start()

    # 2. Instantiate the Assistant Agent
    agent = AssistantAgent(
        name="coder",
        runtime=runtime,
        model=OpenAIClient(model="gpt-4o"),
        system="You are a helpful Python programming assistant."
    )
    
    # 3. Run a user request through the ReAct loop
    result = await agent.run("Write a python function to compute Fibonacci numbers.")
    print(f"Assistant: {result.output}")

    await runtime.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### Agent with Tools

You can register custom tools matching the `Tool` Protocol. Here's a complete mathematical expert agent:

```python
import asyncio
from agent_substrate.integrations.llm.openai.openai_client import OpenAIClient
from agent_substrate.fabric.runtime.local import LocalRuntime
from agent_substratereasoning.agents.assistant.agent import AssistantAgent
from agent_substrate.kernel.tools import ToolExecutionResult
from agent_substrate.kernel.content import TextBlock

# Create a custom tool satisfying the Tool Protocol
class CalculatorTool:
    name: str = "calculator"
    description: str = "Performs mathematical calculations. Supports arithmetic operators."
    input_schema: dict = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Expression to evaluate (e.g. '1234 * 5678')"
            }
        },
        "required": ["expression"]
    }

    async def execute(self, expression: str) -> ToolExecutionResult:
        try:
            # Safe evaluation
            res = eval(expression, {"__builtins__": {}}, {})
            return ToolExecutionResult(
                content=[TextBlock(text=str(res))],
                is_error=False
            )
        except Exception as e:
            return ToolExecutionResult(
                content=[TextBlock(text=f"Error: {e}")],
                is_error=True
            )

async def main():
    runtime = LocalRuntime()
    await runtime.start()

    # Instantiate the agent with the calculator tool registered
    agent = AssistantAgent(
        name="math_expert",
        runtime=runtime,
        model=OpenAIClient(model="gpt-4o"),
        tools=[CalculatorTool()],
        system="Always use your calculator tool to solve math problems."
    )

    result = await agent.run("Calculate 1234 * 5678 and tell me the answer.")
    print(f"Final output:\n{result.output}\n")
    
    # Trace the tools used during execution
    print("Tool trace:")
    for record in result.tool_calls:
        print(f" - Called {record.name}({record.arguments}) -> {record.result} ({record.duration_ms:.2f}ms)")

    await runtime.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 🏛️ Core Architecture (L0-L5)

Ravi's codebase is partitioned into **six strict dependency layers**. Layer imports flow strictly downward; a lower layer never depends on or imports from a higher layer:

```
[L5] platform     ← Scheduling (Temporal.io), Evals, RAG Pipelines, Metrics, Spans
  [L4] guardrails ← Mutation Policies, Economic Budgets, Collusion & Killswitches
    [L3] orchestr.← Hub-Spoke (Orchestrator), Linear/Parallel Flow Pipelines
      [L2] reasoning  ← ReAct loop (AssistantAgent), Compaction Contexts, Hooks
        [L1] fabric     ← Local/Distributed Runtimes, Actor Queues, Sagas, Locks
          [L0] kernel     ← Protocols, ContentBlock types, Identity (AgentId), Plugin Registry
```

### High-Level Layer Guides
We have compiled extensive architectural guides for each of our key layers. Read them here:
*   [🔵 **L0 · Kernel Overview**](docs/overview/kernel.md) — Protocols, pure value structures, and dynamic plugin registries.
*   [🟢 **L1 · Fabric Overview**](docs/overview/fabric.md) — Actor queue mailboxes, message routing, sequence diagrams, and agent lifecycles.
*   [🟡 **L2 · Reasoning Overview**](docs/overview/reasoning.md) — Single-agent cognitive ReAct loops, context strategies, parallel guardrails, and interceptors.
*   [📖 **Layered Architecture Reference**](docs/framework/layered-architecture.md) — Complete migration history, LOC ceilings, and dependency enforcement bounds.

---

## 🏗️ Core Primitives & Concepts

### 1. Identity & Routing Keys
All actors in the mesh are addressed using decoupled value identifiers:
```python
from agent_substrate.kernel import AgentId, TopicId

# Address a specific actor instance
target_agent = AgentId(type="assistant", key="math_expert")

# Address a pub/sub broadcast channel
audit_topic = TopicId(source="security", key="pii_alerts")
```

### 2. Envelope Messaging
Under the hood, all routed data is wrapped in a transport `Message` payload:
```python
from agent_substrate.kernel.message import Message

message = Message(
    target=target_agent,
    payload=TextBlock(text="Check calculations"),
    sender=AgentId(type="user", key="system")
)
```

### 3. Modular Memory & Compaction
Avoid context overflow using active compaction. Register memory contexts to automatically prune prompts:
```python
from agent_substrate.fabric.context import SlidingWindowCompaction

# Keeps only the last 20 messages in active prompting
compactor = SlidingWindowCompaction(max_messages=20)
```

---

## 🕸️ Multi-Agent Workflows

Ravi supports powerful linear, parallel, and routing compositions. Since flows inherit from `BaseFlow`, they can be arbitrarily nested inside one another.

### OrchestratorAgent — Hub & Spoke
An `OrchestratorAgent` registers sub-specialists as individual handoff tools. It analyzes user requests and routes tasks dynamically, synthesizing their results back to the caller.

```python
from agent_substrateorchestration.agents.orchestrator.agent import OrchestratorAgent

orchestrator = OrchestratorAgent(
    name="coordinator",
    runtime=runtime,
    model=openai_client,
    sub_agents=[researcher, writer, review_agent],
    description="Routes complex queries to specialized content assistants."
)
```

### SequentialFlow — Linear Pipeline
Steps execute sequentially in pipeline order. Each step receives the accumulated transcript of previous runs.

```python
from agent_substrateorchestration.agents.flow import SequentialFlow

pipeline = SequentialFlow(
    name="etl_pipeline",
    steps=[document_fetcher, json_converter, formatter_agent]
)
result = await pipeline.run("Load invoice dataset.")
```

### ParallelFlow — Concurrent Execution
Runs all branch agents in parallel concurrently and merges their outputs using standard (`concat`, `vote`) or custom callables.

```python
from agent_substrateorchestration.agents.flow import ParallelFlow

evaluator = ParallelFlow(
    name="peer_review",
    branches=[security_auditor, legal_checker, grammar_advisor],
    merge="concat"  # Combines all review results separating with double newlines
)
```

### ConditionalFlow — Router Branching
Evaluates a synchronous predicate function at runtime, dynamically branching execution to either `if_true` or `if_false` nodes.

```python
from agent_substrateorchestration.agents.flow import ConditionalFlow

smart_router = ConditionalFlow(
    name="inbound_gate",
    predicate=lambda text: "bug" in text.lower(),
    if_true=bug_tracker_agent,
    if_false=general_inbox_agent
)
```

---

## 🔧 Installation & Setup

### Environment Variables
Setup your API keys in a `.env` file inside the root directory:
```bash
OPENAI_API_KEY=sk-proj-...
ANTHROPIC_API_KEY=sk-ant-...
LOG_LEVEL=INFO
```

---

## 🧪 Testing

Ravi utilizes `pytest` to run automated test suites. We assert architecture import bounds,LOC constraints, and ReAct loop convergence:

```bash
# Execute standard test suite
pytest

# Execute architecture linter checks
uv run lint-imports
```

---

## 🛣️ Roadmap

*   **Phase 1: Foundation (Stable)** — Protocols, LocalRuntime messaging fabric, and ReAct loops.
*   **Phase 2: Enterprise Sagas (In Progress)** — Saga coordinators, distributed locking, and persistent snapshots.
*   **Phase 3: Scale (Planned)** — DistributedRuntime with Redis & gRPC backplanes, Temporal scheduler activities.
*   **Phase 4: Optimization (Planned)** — Fine-tuning evals, multimodal vector caches, and interactive web dashboard.

---

**Built with ❤️ for the AI agent engineering community**
