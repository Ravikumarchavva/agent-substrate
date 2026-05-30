# L0 · The Kernel

The absolute bedrock of the Ravi Framework. The **Kernel** defines *what* everything is without implementing *how* anything works. It is a pure, compile-time contract layer containing exclusively Protocols, Abstract Base Classes (ABCs), Type definitions, Enums, and the Plugin Registry.

---

## Architectural Philosophy

To ensure the framework remains maintainable and decoupled at scale, the Kernel is governed by a strict discipline:

1. **Zero I/O & Zero Side Effects**: There are no database calls, no network sockets, no HTTP client initialization, and no LLM API requests inside `src/ravi/kernel/`. 
2. **Pure Python Types & Contracts**: The Kernel specifies contracts and base structures. Concrete behaviors—such as message routing, database storage, and LLM inference—are implemented in layers above.
3. **Downward Import-Only Flow**: Files within `kernel/` are not allowed to import from any higher layers. This is enforced at the CI level by import linting.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#334155", "lineColor": "#64748b"}}}%%
flowchart TD
    L0["🔵 L0 · Kernel (Pure Types, ABCs & Contracts)"]:::l0
    L1["🟢 L1 · Fabric (Messaging & Actor Runtime)"]:::l1
    L2["🟡 L2 · Reasoning (ReAct Cogntive Loop & Memory)"]:::l2
    L3["🟠 L3 · Orchestration (Multi-Agent Workflows)"]:::l3
    L4["🔴 L4 · Guardrails (Security, Limits & Budgets)"]:::l4
    L5["🟣 L5 · Platform (Observability & Scale Services)"]:::l5

    L0 --> L1 --> L2 --> L3 --> L4 --> L5

    classDef l0 fill:#1e3a5f,stroke:#60a5fa,color:#eff6ff
    classDef l1 fill:#14532d,stroke:#4ade80,color:#f0fdf4
    classDef l2 fill:#713f12,stroke:#fbbf24,color:#fffbeb
    classDef l3 fill:#7c2d12,stroke:#fb923c,color:#fff7ed
    classDef l4 fill:#7f1d1d,stroke:#f87171,color:#fff1f2
    classDef l5 fill:#4c1d95,stroke:#c084fc,color:#faf5ff
```

---

## Core Primitives and Abstractions

The type system inside the Kernel acts as a universal translator across all layers.

### 1. Identity & Routing Keys
At the core of the messaging fabric are identity wrappers:
*   `AgentId`: Structured identifier mapping an agent's `type` and a unique identifier `key` (e.g., `assistant/default`).
*   `TopicId`: Pub/sub target containing a specific source and routing key.

### 2. Value Containers
*   `Envelope`: The primary transport mechanism of the fabric. Every message in flight is wrapped in an `Envelope`, which carries the sender/recipient `AgentId`, tracing headers, identity context, temporal metadata (TTL), and the payload itself.
*   `ContentBlock`: A typed, multimodal primitive (such as `TextBlock`, `ImageBlock`, `AudioBlock`, or `VideoBlock`) representing individual segments of content inside a message payload.

### 3. The Multimodal Message System
All LLM-facing communication inherits from the `BaseClientMessage` contract. Each message contains a `role` mapped to native vendor formats:

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
classDiagram
    direction TB
    class BaseClientMessage {
        <<abstract>>
        +role: str
        +type: str
    }
    class SystemMessage {
        +role = "system"
        +content: str
    }
    class UserMessage {
        +role = "user"
        +content: list[MessageContent]
    }
    class AssistantMessage {
        +role = "assistant"
        +content: list[MessageContent]
        +tool_calls: list[ToolCallMessage]
        +finish_reason: str
        +reasoning: str
        +usage: UsageStats
    }
    class ToolCallMessage {
        +role = "tool_call"
        +id: str
        +name: str
        +arguments: dict
    }
    class ToolExecutionResultMessage {
        +role = "tool_response"
        +tool_call_id: str
        +name: str
        +content: list[ContentBlock]
        +is_error: bool
    }

    BaseClientMessage <|-- SystemMessage
    BaseClientMessage <|-- UserMessage
    BaseClientMessage <|-- AssistantMessage
    BaseClientMessage <|-- ToolCallMessage
    BaseClientMessage <|-- ToolExecutionResultMessage
```

---

## Key Interface Contracts

All operational blocks in Ravi implement a Kernel protocol:

| Contract Interface | Category | Purpose |
|-------------------|----------|---------|
| `AgentProtocol` | Execution | The core contract for any actor-mesh node. Defines the async `on_message` interface. |
| `AgentRuntime` | Runtime | The message bus, tracking subscriber topics and point-to-point delivery. |
| `BaseModelClient` | LLM Gateway | The abstract interface for model providers (OpenAI, Anthropic, Gemini). |
| `BaseTool` | Capability | Template-method class for safe, annotated, and risk-rated tools. |
| `BaseMemory` | Memory | Simple async interface for appending and retrieving agent message logs. |
| `BaseGuardrail` | Safety | Async validator checks injecting into execution stages. |
| `BaseMiddleware` | Interceptor | Sequential hook handler wrapping execution boundaries. |

---

## The Plugin Registry

The Kernel houses the process-global Plugin Registry (`_REGISTRY`). It acts as a catalog of available classes (such as agents, tools, guardrails, and memory backends) that can be dynamically registered using Python decorators at import time.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
flowchart LR
    subgraph Decorators ["Dynamic Registrars"]
        RA["@register_agent('assistant')"]
        RG["@register_guardrail('pii')"]
        RT["@register_tool('web_search')"]
        RM["@register_memory('redis')"]
    end

    subgraph Registry ["Process-Global Registry Map"]
        K1["('agent', 'assistant') ──> AssistantAgent"]
        K2["('guardrail', 'pii') ──> PIIGuardrail"]
        K3["('tool', 'web_search') ──> WebSearchTool"]
        K4["('memory', 'redis') ──> RedisMemory"]
    end

    RA --> K1
    RG --> K2
    RT --> K3
    RM --> K4

    style RA fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style RG fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style RT fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style RM fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
```

> [!NOTE]
> The registry ensures early failure: attempting to register a class that does not satisfy its base contract raises a `PluginRegistryError` at import time, preventing silent misconfigurations at runtime.
