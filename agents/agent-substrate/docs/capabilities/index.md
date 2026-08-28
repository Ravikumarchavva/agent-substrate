# Capabilities Layer (L2)

L2 is the **"what agents can do"** layer. Every concrete tool, knowledge pipeline, memory backend, storage adapter, and trigger lives here. Kernel (L0) defines the contracts; agents (L1) drive the ReAct loop; capabilities (L2) provides everything those agents can reach for.

Prefer a visual, concern-organized tour instead? See the **[Capability Map](../capability-map.md)**.

## Ten sub-packages

```
capabilities/
├── tools/          Tool implementations + Skills + ToolChain
├── knowledge/      RAGPipeline, GraphRAGPipeline, chunkers, loaders, reranker
├── memory/         CachedShortTermMemory + DurableSessionStore + RedisSessionStore (session state)
│                   DurableMemoryStore (long-term facts)
├── history/        CachedHistoryProvider + DurableHistoryProvider + RedisHistoryProvider (chat logs)
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
    classDef proto fill:#F3E5F5,stroke:#6A1B9A,color:#4A148C

    L3["fabric (L3)<br/>SequentialFlow · ParallelFlow · ConditionalFlow · EvalRunner"]:::layer

    subgraph L2["capabilities (L2) — concrete implementations of kernel Protocols"]
        direction TB
        T["tools/ · skills/ · chain/<br/>CapabilityDiscovery · 18 built-in tools<br/>SkillManager · ToolChainTool + BridgeSession"]:::cap
        K["knowledge/<br/>RAGPipeline · GraphRAGPipeline<br/>chunkers · loaders · reranker"]:::cap
        MH["memory/ + history/<br/>CachedShortTermMemory · DurableMemoryStore<br/>CachedHistoryProvider"]:::cap
        SS["vector/ · graph/ · storage/<br/>PgVectorStore · AGEGraphStore · S3FileStore"]:::cap
        PE["pipeline/ + llm/<br/>PipelineEngine · DataRefStore<br/>OpenAIChatCompletionClient · embeddings"]:::cap
        TR["triggers/<br/>TriggerScheduler · WebhookRegistry · ConditionMonitor"]:::cap
        T ~~~ K ~~~ MH ~~~ SS ~~~ PE ~~~ TR
    end

    L1["agents (L1)<br/>ReActAgent · OrchestratorAgent · Runtime · ToolInvoker"]:::layer
    L0["kernel (L0) — Protocols<br/>Tool · VectorStore · GraphStore<br/>BlobStore · HistoryProvider · SessionStore"]:::proto
    EXT["External systems<br/>PostgreSQL · Redis · SeaweedFS / S3<br/>OpenAI · Anthropic · Gemini · Ollama"]:::ext

    L3 -->|"imports"| L2
    L2 -->|"imports"| L1
    L1 -->|"imports"| L0
    L2 -.->|"implements Protocols"| L0
    L2 -.->|"network / SQL I/O"| EXT
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
