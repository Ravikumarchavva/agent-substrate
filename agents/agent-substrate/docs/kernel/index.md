# Kernel — Architecture Overview

> **The kernel is L0 — frozen.** It contains only Protocols, dataclasses, and enums. No I/O, no concrete implementations, no imports from any higher layer. Every layer above (agents → capabilities → fabric → serving) builds on it.

## What Lives Here

| Subpackage Module | Core Concern & Focus | What it answers |
|---|---|---|
| [`core/` &mdash; Universal Primitives](01-core.md) | Content blocks, identities, and errors | What data flows everywhere? (ContentBlock, identity, errors) |
| [`llm/` &mdash; LLM Clients](02-llm.md) | Standard client and embedding protocols | How do we talk to an AI model? |
| [`messaging/` &mdash; Conversation Wire](03-messaging.md) | Messages, stream events, envelopes | How do agents communicate? |
| [`tools/` &mdash; Capability Dispatch](04-tools.md) | Standard/hosted tools, approvals, chains | What can an agent do? |
| [`agent/` &mdash; Execution Loop](05-agent.md) | Agent interfaces and supervisions | What IS an agent? How is it supervised? |
| [`storage/` &mdash; Persistence Interfaces](06-storage.md) | Inboxes, logs, history, and store protocols | What does an agent remember? |
| [`runtime/` &mdash; Execution Engines](07-runtime.md) | Priority schedulers, journals, and lifecycles | How does a run stay alive across crashes? |

---

## Subpackage Map

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','secondaryColor': '#E3F2FD','background': '#FAFAFA','fontSize': '14px'}}}%%
graph TB
    classDef foundation fill:#E3F2FD,stroke:#1565C0,stroke-width:2.5px,color:#0D47A1,font-weight:bold
    classDef pkg fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E,font-weight:bold

    CORE["core/\nContentBlock · ChatMessage\nAgentId · TopicId\nUsage · KernelErrors"]:::foundation

    LLM["llm/\nLLMClient · EmbeddingClient\nLLMResponse · GenerationOptions"]:::pkg
    MSG["messaging/\nMessage · Payload types\nStream events · Event envelope"]:::pkg
    TOOLS["tools/\nTool · HostedTool\nProviderDefinedTool\nApproval · Chain · Skill"]:::pkg
    AGENT["agent/\nAgent Protocol · Supervision\nRunMeta · Middleware\nCancellationToken"]:::pkg
    STORE["storage/\nHistoryProvider · Memory\nVectorStore · GraphStore\nBlobStore · TaskStore"]:::pkg
    RT["runtime/\nEventLog · Inbox · Scheduler\nSupervisor · Journal\nFollowGraph · Wakeup"]:::pkg

    CORE --> LLM
    CORE --> MSG
    CORE --> TOOLS
    CORE --> AGENT
    CORE --> STORE
    CORE --> RT
    TOOLS --> MSG
    MSG --> RT
    AGENT --> RT
```

---

## Dependency Rule

Imports within the kernel flow in one direction only:

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph LR
    classDef dep fill:#E8EAF6,stroke:#3949AB,color:#1A237E
    classDef root fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold

    CORE["core/\n(no intra-kernel deps)"]:::root

    TOOLS["tools/"]:::dep
    STREAM["messaging/stream.py"]:::dep
    MSG["messaging/message.py"]:::dep
    LLM["llm/"]:::dep
    STORE["storage/"]:::dep
    SV["agent/supervision.py"]:::dep
    RM["agent/runtime_context.py"]:::dep
    RID["runtime/ids.py"]:::dep
    RT["runtime/ (rest)"]:::dep

    CORE --> TOOLS
    CORE --> STREAM
    CORE --> STORE
    CORE --> SV
    CORE --> RID
    TOOLS --> MSG
    STREAM --> MSG
    MSG --> LLM
    SV --> RM
    RID --> RM
    MSG --> RT
    SV --> RT
    RID --> RT
```

---

## The Staged Implementation Model

Every kernel Protocol has concrete implementations at higher layers. The engine ships Stage 0 and Stage 1 out of the box:

| Kernel Protocol | Stage 0 (in-process) | Stage 1 (durable) |
|---|---|---|
| `EventLog` | In-memory list | Postgres append-only table |
| `Inbox` | In-memory deque | Postgres `(agent_id, msg_id)` table |
| `Scheduler` | asyncio priority queue | `SELECT FOR UPDATE SKIP LOCKED` |
| `Journal` | In-memory dict | Redis (hot) + Postgres (cold) |
| `HistoryProvider` | `InMemoryHistoryProvider` | `PostgresHistoryProvider` |
| `VectorStore` | — | `PgVectorStore` |
| `GraphStore` | — | `AGEGraphStore` |

Switch backends by changing the wiring in `infrastructure/serving_factory.py`. Agent code never changes.
