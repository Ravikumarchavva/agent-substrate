# Diagrams

Use this page when you want the system view before reading implementation details.

## System overview

```mermaid
flowchart LR
    UI[Browser / Frontend] --> BFF[Next.js BFF]
    BFF --> API[FastAPI Server]
    API --> Agent[ReActAgent]
    Agent --> Tools[Tools + HITL]
    Agent --> Memory[Memory + Context]
    API --> Restate[Restate Runtime]
    Restate --> Worker[Worker + Activities]
    Worker --> OpenAI[LLM Provider]
    Worker --> Redis[Redis]
    Worker --> Postgres[PostgreSQL]
    Worker --> Bus[Event Bridge]
    Bus --> UI
```

## Read in this order

1. [Getting Started](../getting-started/index.md) for the first runnable path.
2. [Execution Pipeline](../archive/legacy/execution_pipeline.md) for the detailed sequence view.
3. [Architecture Diagrams](../archive/legacy/ARCHITECTURE_DIAGRAMS.md) for broader component maps.

## Interactive explorer

The previous architecture explorer is preserved here:

- [Legacy interactive explorer](../archive/legacy/architecture_interactive.html)