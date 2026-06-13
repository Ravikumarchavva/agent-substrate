# Kernel Architecture

> **L0 — Pure Contracts.** The kernel contains zero concrete implementations — only `Protocol`, `dataclass`, and `Enum` definitions. Every upper layer depends on this layer; this layer depends on nothing inside the framework.

---

## Architectural Views

To make the system architecture easy to understand, we split the kernel visualization into three distinct perspectives:

### 1. High-Level Architecture
An overview of the central `Agent` protocol and how it connects to core architectural components (context, middleware, model clients, tool execution, and state storage).

![High-Level Architecture](kernel_arch_highlevel.png)

### 2. Runtime Object Snapshot
A lightweight representation of object relations during execution, showing how a message triggers a request through the context, agent, and LLM client.

![Runtime Object Snapshot](kernel_runtime_snapshot.png)

### 3. Domain Model
A static view of the core domain data structures (messages, content blocks, search results, checkpoints, and contexts) and their composition/inheritance relationships, without runtime flow clutter.

![Domain Model](kernel_domain_model.png)

---

## Module Map

```mermaid
graph TD

subgraph PRIMITIVES
    content["content.py<br/>TextBlock, ImageBlock, AudioBlock<br/>DocumentBlock, ToolUseBlock, ToolResultBlock<br/>ChatMessage, JsonObject"]
    message["message.py<br/>Payload, MessageContext<br/>RuntimeRef"]
    usage["usage.py<br/>Usage<br/>prompt tokens, completion tokens"]
    identity["identity.py<br/>AgentId"]
    errors["errors.py<br/>CancellationError, MiddlewareTermination<br/>ToolError, LLMError"]
end

subgraph EXECUTION
    agent["agent.py<br/>Agent Protocol<br/>Checkpoint dataclass"]
    chain["chain.py<br/>Chain, Step<br/>Sequential execution"]
    rc["runtime_context.py<br/>RunContext, CancellationToken"]
    supervision["supervision.py<br/>Supervision<br/>ALLOW, DENY, ESCALATE"]
    approval["approval.py<br/>ApprovalRequest, ApprovalResponse<br/>HITL gate"]
    events["events.py<br/>AgentEvent, RunStarted<br/>RunCompleted, StepEvent"]
end

subgraph MIDDLEWARE
    mw["middleware.py<br/>Middleware CtxT Protocol<br/>AgentRunContext, ChatContext<br/>FunctionContext"]
end

subgraph LLM
    llm["llm.py<br/>LLMClient Protocol<br/>EmbeddingClient Protocol<br/>LLMResponse, EmbeddingResult<br/>GenerationOptions"]
    stream["stream.py<br/>TextDelta, ReasoningDelta<br/>CompletionEvent AsyncIterator"]
end

subgraph TOOLS
    tools["tools.py<br/>Tool descriptor<br/>FunctionTool, AnyTool<br/>JSON Schema validation"]
    skills["skills.py<br/>Skill composable unit"]
end

subgraph MEMORY
    history["history.py<br/>ChatHistory<br/>window management"]
    memory["memory.py<br/>ChatStore Protocol<br/>get, set, clear"]
end

subgraph KNOWLEDGE
    vector["vector.py<br/>VectorStore Protocol<br/>Document, SearchResult<br/>add, search, upsert"]
    graphStore["graph.py<br/>GraphStore Protocol<br/>CypherCapable Protocol<br/>GraphNode, GraphEdge"]
end

content --> message
content --> llm
content --> vector
usage --> llm

identity --> agent
identity --> message
errors --> rc
errors --> mw

rc --> agent
rc --> chain
rc --> llm

supervision --> chain
approval --> chain

message --> agent
message --> events

llm --> stream
llm --> tools

tools --> agent
skills --> agent

history --> memory
memory --> agent

vector --> agent
graphStore --> agent

mw --> agent
mw --> chain
```

---

## Execution Lifecycle

```mermaid
sequenceDiagram
    autonumber
    participant Caller
    participant Chain
    participant MW as Middleware Pipeline
    participant Agent
    participant RC as RunContext
    participant LLM as LLMClient
    participant Tools as Tool Engine
    participant Mem as ChatStore / VectorStore

    Caller->>Chain: run(steps, ctx)
    Chain->>RC: create RunContext (run_id, token, deadline)
    Chain->>MW: process(AgentRunContext, call_next)
    MW->>Agent: on_message(MessageContext, Payload)
    Agent->>Mem: load history / retrieve docs
    Mem-->>Agent: ChatHistory + SearchResults
    Agent->>LLM: generate(messages, options, ctx)
    LLM-->>Agent: LLMResponse (content[], usage)
    alt tool_calls present
        Agent->>Tools: execute(ToolUseBlock)
        Tools-->>Agent: ToolResultBlock
        Agent->>LLM: generate(updated messages)
        LLM-->>Agent: final LLMResponse
    end
    Agent->>Mem: persist message + update history
    Agent-->>MW: Payload (reply)
    MW-->>Chain: pass through (telemetry, logging)
    Chain-->>Caller: final result
```

---

## Protocol Dependency Rules

```mermaid
graph LR
    subgraph L0["L0 — kernel  (this layer)"]
        K[Protocols · Dataclasses · Enums]
    end
    subgraph L1["L1 — agents"]
        A[Concrete Agent impls]
    end
    subgraph L2["L2 — capabilities"]
        C[RAG · Tools · LLM clients]
    end
    subgraph L3["L3 — fabric"]
        F[Flows · Evals · Durable runners]
    end

    K -->|implements| A
    A -->|composes| C
    C -->|orchestrates| F
    L1 -. must NOT import .-> L2
    L2 -. must NOT import .-> L3
    K  -. must NOT import .-> L1
```

> Enforced at CI time by **import-linter** (`uv run lint-imports`).

---

## Key Design Principles

| Principle | How the kernel enforces it |
|---|---|
| **No concrete deps** | Every module uses only `typing.Protocol` — no imports of `httpx`, `openai`, etc. |
| **Immutability** | All value objects are `@dataclass(frozen=True)` or `@dataclass(frozen=True, slots=True)` |
| **Runtime checkable** | Key protocols are `@runtime_checkable` so `isinstance()` works for adapters |
| **Cancellation-first** | `CancellationToken` is threaded into every async API call signature |
| **Multimodal by default** | `content: list[ContentBlock]` — not `str` — is the universal payload type |
| **Usage accounting** | `Usage` is returned alongside every `LLMResponse` for cost tracking |
