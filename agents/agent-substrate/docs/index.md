---
hide:
  - navigation
  - toc
---

# Raavan Agent Framework

<div class="grid cards" markdown>

-   :material-rocket-launch-outline: **Get started in minutes**

    ---

    Install with `uv`, connect to OpenAI, and understand the durable runtime and execution flow quickly.

    [:octicons-arrow-right-24: Execution Pipeline](execution_pipeline.md)

-   :material-robot-outline: **Distributed architecture**

    ---

    See how the UI, FastAPI server, Restate runtime, workers, memory, and event streaming fit together.

    [:octicons-arrow-right-24: Architecture](DISTRIBUTED_ARCHITECTURE.md)

-   :material-tools: **Design patterns**

    ---

    Review the core implementation patterns used across storage, tools, workflows, adapters, and runtime wiring.

    [:octicons-arrow-right-24: Design Patterns](design_patterns.md)

-   :material-shield-check-outline: **Interactive explorer**

    ---

    Open the standalone interactive architecture explorer for drill-down, click-to-expand runtime details.

    [:octicons-arrow-right-24: Open Explorer](architecture_interactive.html)

-   :material-database-clock-outline: **Durable execution**

    ---

    Restate-backed workflows — crash-safe, exactly-once tool execution, automatic replay.

    [:octicons-arrow-right-24: Durable Runtime](DISTRIBUTED_RUNTIME.md)

-   :material-chart-line: **Operations guide**

    ---

    Deployment, observability, infrastructure, and operational references in one place.

    [:octicons-arrow-right-24: Operations](OPERATIONS.md)

</div>

---

## Installation

=== "uv (recommended)"

    ```bash
    git clone https://github.com/Ravikumarchavva/raavan.git
    cd raavan
    uv sync
    ```

=== "with extras"

    ```bash
    # Notebook support
    uv sync --group notebooks

    # Browser automation
    uv sync --group browser

    # S3 / object storage
    uv sync --group storage
    ```

---

## Your first agent

```python
import asyncio
from raavan.core.agents.react_agent import ReActAgent
from raavan.core.memory import UnboundedMemory
from raavan.integrations.llm.openai.openai_client import OpenAIClient

async def main():
    client = OpenAIClient(api_key="sk-...", model="gpt-4o")
    memory = UnboundedMemory()
    agent = ReActAgent(model_client=client, memory=memory, tools=[])

    reply = await agent.run("What is 17 * 23?")
    print(reply)

asyncio.run(main())
```

---

## Architecture at a glance

```mermaid
graph LR
    Browser["🌐 Browser<br>(React UI)"] -->|SSE| BFF["⚡ Next.js BFF"]
    BFF -->|HTTP| API["🔌 FastAPI"]
    API -->|invoke| Restate["💾 Restate<br>(durable runtime)"]
    Restate -->|dispatch| Worker["⚙️ Worker"]
    Worker -->|LLM calls| OpenAI["🤖 OpenAI"]
    Worker -->|memory| Redis["🟥 Redis"]
    Worker -->|persist| PG["🐘 PostgreSQL"]
    Worker -->|events| NATS["📡 NATS"]
    NATS -->|stream| API
```

---

## Why Raavan?

| Feature | Raavan | LangChain | LlamaIndex | Google ADK |
|---|---|---|---|---|
| Durable execution (crash-safe) | ✅ Restate | ❌ | ❌ | ❌ |
| Human-in-the-loop | ✅ native | ⚠️ DIY | ⚠️ DIY | ✅ |
| MCP tool support | ✅ | ✅ | ✅ | ✅ |
| Async-first | ✅ | ⚠️ partial | ⚠️ partial | ✅ |
| Streaming UI | ✅ SSE | ⚠️ | ⚠️ | ✅ |
| Built-in eval framework | ✅ | ⚠️ | ✅ | ✅ |
| Observability (OTEL) | ✅ native | ⚠️ plugin | ⚠️ plugin | ✅ |

---

## Notebooks

Explore the [`examples/`](https://github.com/Ravikumarchavva/raavan/tree/main/examples) folder for 20 Jupyter notebooks covering everything from basic agents to Kubernetes deployments.
