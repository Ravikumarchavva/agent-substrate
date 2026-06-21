# Capabilities Layer (L2)

L2 is the **"what agents can do"** layer. Every concrete tool, knowledge pipeline, memory backend, storage adapter, and trigger lives here. Kernel (L0) defines the contracts; agents (L1) drive the ReAct loop; capabilities (L2) provides everything those agents can reach for.

## Ten sub-packages

```
capabilities/
├── tools/          Tool implementations + Skills + ToolChain
├── knowledge/      RAGPipeline, GraphRAGPipeline, chunkers, loaders, reranker
├── memory/         RedisSessionStore, PostgresMemoryStore (short-term state)
├── history/        RedisHistoryProvider, PostgresHistoryProvider (chat logs)
├── vector/         PgVectorStore — implements kernel VectorStore Protocol
├── graph/          AGEGraphStore — implements kernel GraphStore Protocol
├── storage/        S3FileStore — implements kernel BlobStore Protocol
├── pipeline/       PipelineEngine, PipelineDef, DataRefStore (adapter chains)
├── llm/            OpenAIChatCompletionClient, embedding clients
└── triggers/       TriggerScheduler, WebhookRegistry, ConditionMonitor
```

## How capabilities fit into the stack

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','secondaryColor': '#E3F2FD','tertiaryColor': '#F3E5F5','fontSize': '13px'}}}%%
flowchart TB
    classDef layer fill:#E8EAF6,stroke:#3949AB,color:#1A237E,font-weight:bold
    classDef cap   fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
    classDef ext   fill:#FFF3E0,stroke:#E65100,color:#BF360C,stroke-dasharray:4 2

    L3["fabric (L3)\nflows, evals"]:::layer
    L1["agents (L1)\nReActAgent, Runtime, middleware"]:::layer
    L0["kernel (L0)\nProtocols, contracts"]:::layer

    subgraph L2["capabilities (L2)"]
        style L2 fill:#E8F5E9,stroke:#2E7D32,color:#1B5E20
        T["tools/\nLocal + Hosted + Skills + Chain"]:::cap
        K["knowledge/\nRAG + GraphRAG"]:::cap
        M["memory/ + history/\nSession state + chat logs"]:::cap
        S["vector/ + graph/ + storage/\nStore implementations"]:::cap
        P["pipeline/\nPipelineEngine + DataRefStore"]:::cap
        TR["triggers/\nScheduler + Webhooks + Conditions"]:::cap
    end

    PG["PostgreSQL"]:::ext
    RD["Redis"]:::ext
    S3["MinIO / S3"]:::ext
    OAI["OpenAI / LLM APIs"]:::ext

    L3 --> L2
    L1 --> L2
    L2 --> L0
    S --> PG
    M --> RD
    S --> S3
    T --> OAI
```

## Design rules

**Capabilities only implement kernel Protocols** — `PgVectorStore` implements `VectorStore`, `RedisHistoryProvider` implements `HistoryProvider`, and so on. No layer above L2 ever imports a concrete backend directly; they import the Protocol from kernel and receive the implementation via dependency injection in lifespan.

**No capabilities import from agents or fabric** — the dependency rule strictly flows downward. If you need runtime context inside a tool, it is passed as the `ctx` parameter to `execute()`.

**Auto-discovery, not registration** — `CapabilityDiscovery` scans `capabilities/tools/` at startup. Any package with a `tool.py` containing a class with `{name, description, input_schema, execute}` is automatically registered. No central list to update.

## Pages in this section

| Page | Topic |
|---|---|
| [1 · Tools](01-tools.md) | Tool taxonomy, `CapabilityDiscovery`, `Toolbox` |
| [2 · Skills](02-skills.md) | `SkillTool`, `SKILL.md` format, `SkillManager` |
| [3 · Tool Chain](03-tool-chain.md) | `ToolChainTool`, sandbox bridge, code-mode chaining |
| [4 · Knowledge & RAG](04-knowledge-rag.md) | `RAGPipeline`, `GraphRAGPipeline`, loaders, reranker |
| [5 · Memory & History](05-memory-and-history.md) | Session state, chat logs, Redis/Postgres backends |
| [6 · Storage & Pipeline](06-storage-and-pipeline.md) | `PgVectorStore`, `AGEGraphStore`, `S3FileStore`, `PipelineEngine` |
| [7 · Triggers](07-triggers.md) | `TriggerScheduler`, `WebhookRegistry`, `ConditionMonitor` |
