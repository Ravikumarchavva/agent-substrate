# L2 · Reasoning

The **Reasoning** layer represents the cognitive core of a single agent. It encapsulates the execution loops, prompt context assembly strategies, tool-calling lifecycle, parallel guardrail validation, middleware pipelines, and structured schemas that allow an actor to think, decide, and react.

---

## The Core: AssistantAgent & The ReAct Loop

The primary cognitive agent in Ravi is `AssistantAgent`. Upon receiving an envelope, the agent launches a localized **ReAct (Reasoning and Action)** execution cycle inside its `on_message` handler.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
stateDiagram-v2
    [*] --> ContextBuilding : Message Received
    ContextBuilding --> RunGuardrails_Input : Assemble memory & prompt
    
    state "Cognitive Cycle" as Loop {
        state "Think (LLM Inference)" as Think
        state "Act (Tool Call Selection)" as Act
        state "Observe (Tool Execution)" as Observe

        Think --> Act : Output contains tool call request
        Act --> RunGuardrails_ToolCall : Check tool permissions
        RunGuardrails_ToolCall --> Observe : Approved
        Observe --> Think : Append observation result
    }

    RunGuardrails_Input --> Think : Input cleared
    Think --> RunGuardrails_Output : Direct text response generated
    RunGuardrails_Output --> [*] : Return reply envelope
```

---

## Memory and Context Strategies

An agent's reasoning is heavily dependent on how context is assembled before prompting the LLM. Instead of sending an unmanaged list of historical messages, Ravi uses modular **Context Strategies** to build the active window.

*   `SlidingWindowContext`: Evicts the oldest messages once a target message count is reached.
*   `TokenBudgetContext`: Calculates tokens dynamically (using tiktoken or provider-specific encoders) and prunes historical blocks to fit strict model token ceilings.
*   `SummarizingContext`: Compresses older turns of a conversation into a running semantic paragraph, appending it as a system block while keeping recent turns intact.
*   `HybridContext`: Combines vector search retrieval (RAG) with local message histories to construct highly contextual prompts dynamically.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#334155"}}}%%
flowchart TD
    M1["Unbounded Raw Memory"] --> SM["SessionManager"]
    SM --> CB{"Active Context Strategy"}
    CB -->|SlidingWindow| CS1["Prune by Count"]
    CB -->|TokenBudget| CS2["Prune by Token Limits"]
    CB -->|Summarize| CS3["LLM Compaction Prompt"]
    
    CS1 --> OW["Final Formatted LLM Context Window"]
    CS2 --> OW
    CS3 --> OW
```

---

## Three-Stage Guardrail Pipelines

Safety checking is implemented as non-blocking, parallel guardrail pipelines that execute at three distinct entry points of the cognitive loop.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
flowchart TD
    U["User Input / Incoming Envelope"] --> IG{"INPUT Guardrails<br/>(Parallel)"}
    IG -->|All Pass| LLM["LLM Call (Think)"]
    IG -->|Tripwire| ABORT1["🛑 Abort - Return Error System Envelope"]

    LLM --> OG{"OUTPUT Guardrails<br/>(Parallel)"}
    OG -->|All Pass| TC{"Tool Calls Found?"}
    OG -->|Tripwire| ABORT2["🛑 Abort - Redact / Reject Output"]

    TC -->|Yes| TG{"TOOL_CALL Guardrails<br/>(Parallel)"}
    TC -->|No| RESP["Return Response to Fabric Node"]

    TG -->|All Pass| EXEC["Execute Target Tool"]
    TG -->|Tripwire| ABORT3["🛑 Block Tool Execution - Send Error to LLM"]

    EXEC --> LLM

    style ABORT1 fill:#991b1b,stroke:#f87171,color:#fff1f2
    style ABORT2 fill:#991b1b,stroke:#f87171,color:#fff1f2
    style ABORT3 fill:#92400e,stroke:#fbbf24,color:#fffbeb
    style IG fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style OG fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style TG fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
```

> [!IMPORTANT]
> **Parallel Execution**: Each guardrail stage executes its checks concurrently using `asyncio.gather`. Expensive checks (e.g., LLM-as-a-judge evaluators) do not block or serialize fast checks (e.g., regex pattern matching).
> **Immutable Context**: Guardrails receive a frozen `GuardrailContext` snapshot, ensuring they cannot mutate the state of the active agent mid-check.

---

## The Middleware Onion Interceptor

Execution pipelines inside the reasoning loop are wrapped in an onion-style interceptor pattern using the `ExecutionMiddlewarePipeline`. This is used to layer cross-cutting concerns around LLM actions.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
sequenceDiagram
    participant P as Pipeline Runner
    participant M1 as AuditLogger (L1)
    participant M2 as RateLimiter (L2)
    participant M3 as CacheManager (L3)
    participant E as Inference Execution

    P->>M1: before()
    M1->>M2: before()
    M2->>M3: before()
    M3->>E: Execute LLM Call / Tool Action
    E-->>M3: Return Raw Result
    M3->>M2: after() (Store to Cache)
    M2->>M1: after() (Check and update quotas)
    M1-->>P: after() (Log temporal metrics)

    note over M1,M3: on_error() runs in reverse if any inner step raises an exception.
```

If an error is thrown during execution, the pipeline halts and calls `on_error(ctx, exception)` in reverse order, allowing retry policies to trap, log, and recover before propagating failures to the fabric.

---

## Event Hooks and Observability

The reasoning layer emits operational telemetry at critical boundaries via the `HookManager`. Other layers subscribe to these hooks to power platform evaluations, dashboards, and live debugging portals:

*   `on_run_start`: Triggered when an agent receives an initial envelope and spawns its thread.
*   `on_llm_start` / `on_llm_end`: Measures prompt tokens, completion latency, and model efficiency.
*   `on_tool_start` / `on_tool_end`: Records tool execution times, input parameters, and failure states.
*   `on_handoff`: Captures transfers of delegation between separate actors.
