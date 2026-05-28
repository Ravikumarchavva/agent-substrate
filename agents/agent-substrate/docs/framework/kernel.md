# The Kernel

The kernel is the frozen contract layer of ravi-engine. It defines *what* everything is — ABCs, Protocols, dataclasses, enums — without implementing *how* anything works. Every concrete feature lives in `extensions/`, `integrations/`, `catalog/`, or `server/`. The kernel itself contains no databases, no HTTP calls, no Redis, no LLM API calls.

The reason it exists is discipline. When a new feature lands — a new memory backend, a new guardrail strategy, a new agent type — the developer is forced to ask: does this fit an existing contract, or does the contract need to grow? The kernel stays small by design. If you find yourself wanting to add a `for` loop over agent instances or a `requests.get()` call inside `kernel/`, you are in the wrong layer.

---

## Where the Kernel Sits

The framework is five layers deep. Imports flow strictly downward — the kernel never imports from anything above it.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#334155", "lineColor": "#64748b", "secondaryColor": "#0f172a", "tertiaryColor": "#1e293b", "edgeLabelBackground": "#0f172a", "clusterBkg": "#0f172a"}}}%%
flowchart TD
    S["🌐 server / services\nFastAPI routes, ORM, DI wiring"]:::server
    CA["📦 catalog\ntools, skills, connectors, pipelines"]:::catalog
    IN["🔌 integrations\nOpenAI, Redis, Postgres, S3, MCP"]:::integ
    EX["⚡ extensions\nAssistantAgent, guardrails, middleware, RAG"]:::ext
    KE["🧱 kernel\nABCs · Protocols · dataclasses"]:::kernel

    S --> CA
    CA --> IN
    IN --> EX
    EX --> KE

    classDef server fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    classDef catalog fill:#7c3aed,stroke:#a78bfa,color:#f5f3ff
    classDef integ  fill:#065f46,stroke:#34d399,color:#ecfdf5
    classDef ext    fill:#92400e,stroke:#fbbf24,color:#fffbeb
    classDef kernel fill:#991b1b,stroke:#f87171,color:#fff1f2
```

`shared/` and `configs/` are orthogonal — they sit beside the layers and can be imported by anyone above kernel.

**Enforcement**: `uv run lint-imports` (import-linter) fails CI if anything in `ravi.kernel` imports from `ravi.extensions`, `ravi.integrations`, `ravi.catalog`, `ravi.server`, `ravi.services`, `ravi.shared`, `ravi.configs`, or `ravi.logger`. The architecture tests in `tests/architecture/test_kernel_invariants.py` add a hard ceiling on LOC (15k) and file count (110) so the kernel cannot silently balloon.

---

## What Lives in the Kernel

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
mindmap
  root((kernel/))
    agents
      ActorAgent ABC
      StreamChannel Protocol
      StreamEnvelope
      AgentRunResult
    runtime
      AgentRuntime Protocol
      BaseRuntime ABC
      LocalRuntime
      Envelope
      MessageContext
      AgentId · TopicId
      Dispatcher · Supervisor
      Mailbox · Backpressure
      Saga · ResourceLock
      Lifecycle state machine
    messages
      SystemMessage
      UserMessage
      AssistantMessage
      ToolCallMessage
      ToolExecutionResultMessage
      ContentBlock types
      Encoders OpenAI · Anthropic · Gemini
    tools
      BaseTool ABC
      ToolRisk · HitlMode
      ToolAnnotations · ToolResult
      ResettableTool Protocol
    memory
      BaseMemory ABC
      UnboundedMemory
      LineageStore Protocol
    context
      ModelContext ABC
    guardrails
      BaseGuardrail ABC
      GuardrailType · GuardrailContext
      GuardrailResult
    middleware
      BaseMiddleware ABC
      ExecutionMiddlewarePipeline
    execution
      ExecutionContext
      ExecutionMiddlewarePipeline
    plugin
      register_agent
      register_guardrail
      register_middleware
      register_memory
      register_context
      register_tool
      register_provider
    safeguards
      MutationPolicy Protocol
      MutationKind · MutationPermission
      CircuitBreaker
    llm
      BaseModelClient ABC
      ProviderConfig
    agent_catalog
      AgentCatalog
      AgentCatalogRegistry
    contracts
      EventEnvelope
      TrustContext
      CoordinationContracts
    events
      EventFabric Protocol
    storage
      FileStore ABC
      LocalFileStore
    structured
      StructuredOutputResult
    hooks
      HookManager
    economic
      CostLedger · UsageSignal
    governance
      GovernanceContracts
    scheduler
      SchedulerContracts
    observability
      SpanContract
      KillSwitch · ReplayLog
    ranking
      RankingContracts
    semantics
      SemanticContracts
    metadata
      MetadataStore Protocol
    batch
      BatchConfig · BatchItem
```

---

## The Actor: Every Agent is a Fabric Node

The single most important contract in the kernel is `ActorAgent`. Before the actor migration (completed 2026-05-28), the framework had two disconnected hierarchies: a callable `BaseAgent → ReActAgent` and an actor-model `RuntimeAgent → RuntimeAssistantAgent`. Features added to one did not appear in the other. That divergence is gone. There is now one hierarchy.

Every agent is an actor:
- it lives inside a **runtime** (required, never `None`)
- it is addressed by an **`AgentId`** (`type/key`, e.g. `assistant/default`)
- it receives work through a single entry point: **`on_message(ctx, content)`**
- it communicates outward only via **`send()`** (point-to-point) or **`publish()`** (broadcast)

External callers never call `agent.run()` directly. They enter the fabric through a `UserProxyAgent`, which is itself an actor that fires messages on the caller's behalf.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569", "edgeLabelBackground": "#1e293b"}}}%%
classDiagram
    direction TB

    class ActorAgent {
        <<abstract>>
        +name: str
        +runtime: AgentRuntime
        +key: str
        +catalog: AgentCatalogRegistry
        +id: AgentId
        +start() async
        +stop() async
        +on_message(ctx, content)* async
        +send(message, recipient) async
        +publish(message, topic) async
    }

    class StreamChannel {
        <<Protocol>>
        +emit(event) async
        +close()
    }

    class StreamEnvelope {
        <<dataclass>>
        +task: str
        +channel: StreamChannel
    }

    class AssistantAgent {
        +on_message(ctx, content) async
        -_run_impl(text) async
        -_run_stream_impl(text, channel) async
    }

    class UserProxyAgent {
        +ask(text, recipient) async
        +ask_stream(text, recipient, channel) async
        +on_message(ctx, content) async
    }

    class OrchestratorAgent {
        +sub_agents: list
        +on_message(ctx, content) async
    }

    ActorAgent <|-- AssistantAgent : extends
    ActorAgent <|-- UserProxyAgent : extends
    ActorAgent <|-- OrchestratorAgent : extends
    AssistantAgent ..> StreamChannel : emits to
    UserProxyAgent ..> StreamEnvelope : creates
```

The class-level contract is minimal — only `on_message()` is abstract. Everything else (`start`, `stop`, `send`, `publish`) is concrete and shared. A routing-only agent like `OrchestratorAgent` has almost no code; `AssistantAgent` puts the full ReAct loop inside `on_message()`.

---

## The Runtime: How Messages Flow

The `LocalRuntime` is the in-process implementation of the `AgentRuntime` protocol. It is the only runtime you need for development, tests, and scripts. Production swaps in a `DistributedRuntime` or `GrpcRuntime` that inherits from the same `BaseRuntime` ABC — the rest of the codebase never notices the difference.

### Point-to-point: `send_message`

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569", "edgeLabelBackground": "#1e293b"}}}%%
sequenceDiagram
    autonumber
    participant C as Caller
    participant R as LocalRuntime
    participant MW as Routing Middleware
    participant D as Dispatcher
    participant MB as Mailbox
    participant H as on_message()

    C->>R: send_message(msg, recipient=AgentId)
    R->>R: _ensure_started()
    R->>R: _ensure_agent(recipient) — lazy create if new
    R->>R: wrap msg → Envelope[ContentBlock]
    R->>MW: _apply_routing_middleware(envelope)
    MW-->>R: allow / drop
    R->>R: create asyncio.Future for reply
    R->>D: dispatch(envelope)
    D->>MB: mailbox.put(envelope)
    MB->>H: await on_message(ctx, content)
    H-->>MB: return result
    MB->>R: future.set_result(result)
    R-->>C: return result
```

### Pub/sub: `publish_message`

`publish_message` is fire-and-forget. The runtime fans the message out to every agent type subscribed to the topic. Each subscriber gets an `AgentId` with `key = topic.source`, so the same message arrives at every interested agent without the sender knowing who they are.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
flowchart LR
    subgraph Sender
        P["publish_message\ntopic=TopicId(...)"]
    end

    subgraph Runtime
        MW2["Routing\nMiddleware"]
        D2["Dispatcher"]
    end

    subgraph Subscribers
        A1["AgentA\non_message()"]
        A2["AgentB\non_message()"]
        A3["AgentC\non_message()"]
    end

    P --> MW2 --> D2
    D2 -->|fan-out| A1
    D2 -->|fan-out| A2
    D2 -->|fan-out| A3

    style P fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style MW2 fill:#7c3aed,stroke:#a78bfa,color:#f5f3ff
    style D2 fill:#065f46,stroke:#34d399,color:#ecfdf5
    style A1 fill:#92400e,stroke:#fbbf24,color:#fffbeb
    style A2 fill:#92400e,stroke:#fbbf24,color:#fffbeb
    style A3 fill:#92400e,stroke:#fbbf24,color:#fffbeb
```

### Agent lifecycle inside LocalRuntime

Every agent instance goes through a state machine managed by `AgentActivationContract`. The runtime uses it to know whether to start a new loop, whether a lease is held, and whether to restart after a crash.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9"}}}%%
stateDiagram-v2
    direction LR
    [*] --> DORMANT : registered
    DORMANT --> ACTIVATING : first message arrives
    ACTIVATING --> ACTIVE : lease acquired,\nmailbox created,\nloop spawned
    ACTIVE --> SUSPENDED : handler crashed
    SUSPENDED --> ACTIVATING : supervisor restarts\n(within max_restarts)
    SUSPENDED --> DORMANT : restart limit hit —\nsupervisor escalates
    ACTIVE --> HIBERNATING : hibernate() called
    HIBERNATING --> DORMANT : lease released,\nmailbox closed
    DORMANT --> [*] : runtime.stop()
```

When a `LeaseRegistry` is configured, the `ACTIVATING → ACTIVE` transition also acquires a distributed lease. If another worker already holds the lease, `LeaseAcquisitionFailed` is raised and the caller can route elsewhere — this is the basis for hot-standby failover in the distributed deployment.

### The Envelope

Every message in flight is an `Envelope`. It carries the content but also a rich metadata coat: identity, trust chain, placement hints, temporal semantics (TTL, not-before), OTel trace context, and a correlation ID for response matching.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
classDiagram
    class Envelope {
        +sender: AgentId | None
        +target: AgentId | TopicId | None
        +content: list[ContentBlock]
        +correlation_id: str
        +causation_id: str | None
        +trace_context: dict
        +temporal: TemporalSemantics
        +locality: LocalityHint
        +trust: TrustContext | None
        +provenance: ProvenanceChain | None
        +identity: IdentityContext | None
        +activation: AgentActivationContract | None
        +priority: int
        +is_expired: bool
        +is_ready: bool
        +to_event_envelope()
    }

    class TemporalSemantics {
        +ttl_seconds: float | None
        +not_before: datetime | None
        +is_expired(now) bool
        +is_ready(now) bool
    }

    class TrustContext {
        +level: TrustLevel
        +verified: bool
    }

    class IdentityContext {
        +principal: PrincipalId
        +delegation_chain: tuple
        +effective_tenant_id: str
        +effective_workspace_id: str
    }

    Envelope *-- TemporalSemantics
    Envelope *-- TrustContext
    Envelope *-- IdentityContext
```

---

## Messages and Content: The Multimodal Type System

Every piece of data that an agent sends to an LLM or receives back from one is a `BaseClientMessage`. There are five concrete types, each with a `role` string that maps to the LLM provider's convention.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
classDiagram
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
        +content: list[MessageContent] | None
        +tool_calls: list[ToolCallMessage] | None
        +finish_reason: str
        +reasoning: str | None
        +usage: UsageStats | None
        +cached: bool
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
        +app_data: dict | None
        +media: list[MediaContent] | None
    }

    BaseClientMessage <|-- SystemMessage
    BaseClientMessage <|-- UserMessage
    BaseClientMessage <|-- AssistantMessage
    BaseClientMessage <|-- ToolCallMessage
    BaseClientMessage <|-- ToolExecutionResultMessage
    AssistantMessage *-- ToolCallMessage : has tool_calls
```

`MessageContent` is a union type: `str | ImageContent | AudioContent | VideoContent | DocumentContent`. The kernel's `ContentBlock` type is narrower — it is the typed primitive for the runtime fabric (`TextBlock | ImageBlock | …`). Provider-specific wire encoding lives in `kernel/messages/encoders/` — the messages themselves are provider-agnostic.

---

## Tools: Template Method + Risk Tier + HITL

`BaseTool` is the Template Method base class for everything the agent can call. The single abstract method is `execute(**kwargs) → ToolResult`. The base class handles input validation, schema publication, and HITL mode flags.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
classDiagram
    class BaseTool {
        <<abstract>>
        +name: str
        +description: str
        +input_schema: dict
        +risk: ToolRisk
        +hitl_mode: HitlMode
        +is_critical: bool
        +compensating_tool: str | None
        +resource_uri: str | None
        +run(**kwargs) async
        +execute(**kwargs)* async
        +get_schema() Tool
        +get_openai_schema() dict
        +get_mcp_schema() dict
        -_validate_input(kwargs)
    }

    class ToolRisk {
        <<enum>>
        SAFE
        SENSITIVE
        CRITICAL
        +color: str
    }

    class HitlMode {
        <<enum>>
        BLOCKING
        CONTINUE_ON_TIMEOUT
        FIRE_AND_CONTINUE
    }

    class ToolResult {
        +content: list[ContentBlock]
        +is_error: bool
        +app_data: dict | None
        +media: list[MediaContent] | None
        +data_ref: DataRef | None
    }

    class ResettableTool {
        <<Protocol>>
        +reset()
    }

    BaseTool ..> ToolRisk : classifies with
    BaseTool ..> HitlMode : gates with
    BaseTool ..> ToolResult : returns
    BaseTool ..|> ResettableTool : optionally implements
```

### Risk and HITL interaction

`ToolRisk` is purely informational — it drives UI badge colours and lets policy engines categorise tools without inspecting them. `HitlMode` is operational:

| Mode | What happens when a CRITICAL tool fires |
|------|-----------------------------------------|
| `BLOCKING` | Agent suspends. Non-response = **denied** (veto semantics). Use for send-email, file-delete, payments. |
| `CONTINUE_ON_TIMEOUT` | Agent waits up to `hitl_timeout_seconds`. Timeout = **auto-approved**. Use for time-sensitive UI confirmations. |
| `FIRE_AND_CONTINUE` | Agent sends the SSE event and immediately continues. Use for purely informational UI updates. |

The kernel defines the contracts. The actual approval flow (sending SSE events, waiting for HTTP responses) lives in `catalog/tools/human_input/` and the `server/sse/bridge.py` HITL bridge.

### Runtime integration fields

Two fields on `BaseTool` hook into the `LocalRuntime` subsystems directly:

- **`is_critical = True`** → the runtime wraps execution in a `SagaCoordinator` step for exactly-once semantics. If the process crashes mid-execution, the saga store replays to completion or rolls back via `compensating_tool`.
- **`resource_uri`** → the runtime acquires an exclusive `ResourceLockManager` lock before executing and releases it after. Prevents concurrent modifications to the same file, DB row, or external resource.

---

## Memory: One Async Interface

`BaseMemory` is a single async interface with four methods. There are no sync variants, no `add_message_async` / `add_message_sync` pairs, no `close()`. The only lifecycle method is `clear()`.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
classDiagram
    class BaseMemory {
        <<abstract>>
        +add_message(msg) async
        +get_messages(limit?) async
        +clear() async
        +get_token_count() async
        +size() async
    }

    class UnboundedMemory {
        +add_message(msg) async
        +get_messages(limit?) async
        +clear() async
        +get_token_count() async
    }

    class RedisMemory {
        +add_message(msg) async
        +get_messages(limit?) async
        +restore() async
        +clear() async
    }

    class PostgresMemory {
        +add_message(msg) async
        +get_messages(limit?) async
        +clear() async
    }

    BaseMemory <|-- UnboundedMemory : kernel reference impl
    BaseMemory <|-- RedisMemory : integrations/memory/
    BaseMemory <|-- PostgresMemory : integrations/memory/
```

`UnboundedMemory` is the only concrete implementation inside the kernel — a plain in-memory list. It is intentionally simple: no trimming, no TTL, no persistence. Real deployments use `RedisMemory` (hot store) layered over `PostgresMemory` (cold store) via the `HybridContext` strategy in `extensions/context/`.

The `LineageStore` Protocol (also in `kernel/memory/`) is a separate contract for immutable audit trails — session lineage, parent-child message relationships. It is not `BaseMemory`; it is append-only and never cleared.

---

## Guardrails: Three Injection Points

Guardrails are async checks that fire at specific points in the agent loop. A check either **passes**, **fails** (soft — logged, loop continues), or **trips** (hard — loop aborts immediately).

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
flowchart TD
    U["User Input"] --> IG{"INPUT\nguardrails\n(parallel)"}
    IG -->|all pass| LLM["LLM Call"]
    IG -->|tripwire| ABORT1["🛑 Abort — return error"]

    LLM --> OG{"OUTPUT\nguardrails\n(parallel)"}
    OG -->|all pass| TC{Tool calls?}
    OG -->|tripwire| ABORT2["🛑 Abort — return error"]

    TC -->|yes| TG{"TOOL_CALL\nguardrails\n(parallel)"}
    TC -->|no| RESP["Return to user"]

    TG -->|all pass| EXEC["Execute tool"]
    TG -->|tripwire| ABORT3["🛑 Block tool — report to LLM"]

    EXEC --> LLM

    style ABORT1 fill:#991b1b,stroke:#f87171,color:#fff1f2
    style ABORT2 fill:#991b1b,stroke:#f87171,color:#fff1f2
    style ABORT3 fill:#92400e,stroke:#fbbf24,color:#fffbeb
    style IG fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style OG fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style TG fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
```

All three injection points run guardrails in parallel via `asyncio.gather` — an expensive external-check guardrail (like an LLM judge) does not serialize the fast regex checks. The `GuardrailContext` passed to each check is a frozen snapshot: no guardrail can mutate agent state.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
classDiagram
    class BaseGuardrail {
        <<abstract>>
        +name: str
        +guardrail_type: GuardrailType
        +check(ctx) async*
        #_pass(message) GuardrailResult
        #_fail(message, tripwire) GuardrailResult
    }

    class GuardrailType {
        <<enum>>
        INPUT
        OUTPUT
        TOOL_CALL
    }

    class GuardrailContext {
        <<frozen Pydantic>>
        +agent_name: str
        +input_text: str | None
        +output_text: str | None
        +tool_name: str | None
        +tool_arguments: dict | None
        +raw_message: BaseClientMessage | None
    }

    class GuardrailResult {
        +guardrail_name: str
        +passed: bool
        +tripwire: bool
        +message: str
        +metadata: dict
        +timestamp: datetime
    }

    BaseGuardrail ..> GuardrailContext : receives
    BaseGuardrail ..> GuardrailResult : returns
    BaseGuardrail ..> GuardrailType : declares
```

---

## Middleware: Before / Execute / After / OnError

`ExecutionMiddlewarePipeline` is a generic sequential runner. The same engine powers both agent middleware (audit logging, caching, rate limiting, retry) and workflow middleware (pipeline step wrapping). Middleware runs `before()` on each entry in order, then `execute_fn()`, then `after()` in reverse — the standard onion/interceptor pattern.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
sequenceDiagram
    participant P as Pipeline.run()
    participant M1 as AuditLogger.before()
    participant M2 as RateLimiter.before()
    participant M3 as Cache.before()
    participant E as execute_fn(ctx)
    participant M3A as Cache.after()
    participant M2A as RateLimiter.after()
    participant M1A as AuditLogger.after()

    P->>M1: before(ctx) → ctx
    M1->>M2: before(ctx) → ctx
    M2->>M3: before(ctx) → ctx
    M3->>E: execute_fn(ctx) → result
    E-->>M3A: result
    M3A->>M2A: after(ctx, result) → result
    M2A->>M1A: after(ctx, result) → result
    M1A-->>P: final result

    note over M1,M3: on_error() runs in reverse\nif execute_fn throws
```

If `execute_fn` raises, each middleware's `on_error(ctx, exc)` runs in reverse order. The first non-`None` return from any `on_error` becomes the result; if all return `None`, the exception re-raises. This lets a retry middleware return a sentinel that the pipeline runner interprets as "try again."

---

## The Plugin Registry

The plugin registry maps `(category, name) → class`. Decorators register classes at import time. The registry is process-global and never cleared except during test teardown.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
flowchart LR
    subgraph Decorators
        RA["@register_agent('assistant')"]
        RG["@register_guardrail('pii')"]
        RM["@register_middleware('audit')"]
        RT["@register_tool('web_search')"]
        RMem["@register_memory('redis')"]
        RC["@register_context('sliding_window')"]
        RP["@register_provider('openai')"]
    end

    subgraph Registry ["_REGISTRY dict"]
        K1["('agent', 'assistant') → AssistantAgent"]
        K2["('guardrail', 'pii') → PIIGuardrail"]
        K3["('middleware', 'audit') → AuditLogger"]
        K4["('tool', 'web_search') → WebSearchTool"]
        K5["('memory', 'redis') → RedisMemory"]
        K6["('context', 'sliding_window') → SlidingWindowContext"]
        K7["('provider', 'openai') → OpenAIClient"]
    end

    RA --> K1
    RG --> K2
    RM --> K3
    RT --> K4
    RMem --> K5
    RC --> K6
    RP --> K7

    style RA fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style RG fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style RM fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style RT fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style RMem fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style RC fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style RP fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
```

Each decorator is bound to a base class (e.g. `register_agent` → `ActorAgent`, `register_guardrail` → `BaseGuardrail`). Registration fails at import time with a `PluginRegistryError` if the class does not subclass the required base. Duplicate names also fail immediately. This makes misconfiguration loud and early — not silent at runtime.

`get_registered(category, name)` retrieves a class. Construction and wiring is the caller's responsibility — the registry holds classes, not instances.

---

## The AgentCatalog: Per-Agent Capability Registry

Every `ActorAgent` carries an `AgentCatalogRegistry` — a per-agent inventory of its models, memories, contexts, and tools. Unlike the global plugin registry (which holds classes), the catalog holds configured *instances* ready to use.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
classDiagram
    class AgentCatalogRegistry {
        +register_model(name, client)
        +register_memory(name, memory)
        +register_context(name, context)
        +register_tool(tool)
        +get_model(name) BaseModelClient
        +get_memory(name) BaseMemory
        +get_context(name) ModelContext
        +get_tool(name) BaseTool | None
        +all_tools() list[BaseTool]
        +from_tools_and_skills(tools) classmethod
    }

    class ActorAgent {
        +catalog: AgentCatalogRegistry
    }

    class BaseModelClient {
        <<abstract>>
        +generate(messages, tools?) async
        +generate_stream(messages, tools?) async
    }

    class BaseMemory {
        <<abstract>>
    }

    class ModelContext {
        <<abstract>>
        +build(messages, memory) async
    }

    class BaseTool {
        <<abstract>>
    }

    ActorAgent *-- AgentCatalogRegistry
    AgentCatalogRegistry --> BaseModelClient : holds
    AgentCatalogRegistry --> BaseMemory : holds
    AgentCatalogRegistry --> ModelContext : holds
    AgentCatalogRegistry --> BaseTool : holds many
```

`AssistantAgent` reads from the catalog to get its primary model client (`catalog.get_model("primary")`), memory (`catalog.get_memory("memory")`), and all registered tools. This design means two agents of the same type can have entirely different backends (different LLMs, different memory backends) without subclassing.

---

## Safeguards: Mutation Gates and Circuit Breakers

The kernel has two safeguard contracts that apply to agent self-modification and downstream service stability.

### MutationPolicy — gating self-evolution

An agent that wants to modify itself (rewrite its system prompt, add a tool, push a weight update, or visibly diverge from baseline) must first ask a `MutationPolicy` for permission. The policy returns a `MutationPermission`.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
flowchart LR
    A["AssistantAgent\nrewrite_system_prompt()"] -->|MutationRequest| MP["MutationPolicy\n.evaluate()"]
    MP -->|granted=True| APPLY["Apply mutation"]
    MP -->|granted=False\nreason='forbidden_kind'| DENY["🛑 Reject — raise"]

    style DENY fill:#991b1b,stroke:#f87171,color:#fff1f2
    style APPLY fill:#065f46,stroke:#34d399,color:#ecfdf5
    style MP fill:#7c3aed,stroke:#a78bfa,color:#f5f3ff
```

| `MutationKind` | Default gate |
|----------------|-------------|
| `PROMPT_REWRITE` | Allowed (policy configurable) |
| `TOOL_ADD` | Allowed (policy configurable) |
| `TOOL_REMOVE` | Allowed (policy configurable) |
| `WEIGHT_UPDATE` | **Forbidden by default** — must go through operator model registry |
| `BEHAVIOR_DIVERGENCE` | Triggers audit alert |

`family_depth` in the request prevents depth-escalation attacks where a chain of agents-spawning-agents tries to escape the mutation ceiling by delegating one more level down.

### Circuit Breaker — protecting downstream services

`kernel/safeguards/_breaker.py` defines the `CircuitBreaker` contract. It protects downstream calls (LLM providers, external APIs, databases) from cascading failures.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9"}}}%%
stateDiagram-v2
    direction LR
    [*] --> CLOSED : initial state\n(calls pass through)
    CLOSED --> OPEN : failure_threshold\nexceeded in window
    OPEN --> HALF_OPEN : reset_timeout elapsed
    HALF_OPEN --> CLOSED : probe call succeeds
    HALF_OPEN --> OPEN : probe call fails
```

---

## Supporting Contracts

These contracts sit in the kernel but are used less frequently day-to-day. They become important at scale and in multi-tenant deployments.

| Module | What it defines | When you need it |
|--------|-----------------|-----------------|
| `kernel/contracts/_event.py` | `EventEnvelope[T]` — the canonical wire format for cross-service events | When serialising an `Envelope` for Redis Streams or gRPC |
| `kernel/contracts/_trust.py` | `ProvenanceChain`, `PrincipalTrustContext` | When a guardrail needs to inspect *who* sent a message |
| `kernel/contracts/_coordination.py` | `TemporalSemantics`, `LocalityHint`, `PlacementContract` | When messages have TTLs, scheduling constraints, or datacenter placement needs |
| `kernel/events/_fabric.py` | `EventFabric`, `DurableEventLog`, `RealtimeFanout` Protocols | When implementing a new event bus backend |
| `kernel/economic/` | `CostLedger`, `UsageSignal` | Chargeback and token-cost tracking per agent run |
| `kernel/governance/` | Governance policy contracts | When tenant-level operator rules gate capability access |
| `kernel/scheduler/` | Scheduler trigger contracts | When tools or agents need time-based activation |
| `kernel/observability/` | `SpanContract`, `KillSwitch`, `ReplayLog` | OTel span shaping; emergency kill-switch for runaway agents; replay audit log |
| `kernel/semantics/` | Semantic search contracts | When the catalog needs embedding-based capability discovery |
| `kernel/ranking/` | Ranking strategy contracts | When tool selection or memory retrieval needs a scoring function |
| `kernel/metadata/` | `MetadataStore` Protocol | When agents need to persist and retrieve tagged metadata outside memory |
| `kernel/structured/` | `StructuredOutputResult` | When an agent must return a validated Pydantic model instead of free text |
| `kernel/storage/` | `FileStore`, `LocalFileStore`, `Document`, `TenantContext` | When tools or agents read/write files with tenant isolation |
| `kernel/batch/` | `BatchConfig`, `BatchItem`, `BatchResult` | When running the same agent over thousands of inputs in parallel |
| `kernel/hooks.py` | `HookManager`, lifecycle hook types | When you need `on_run_start`, `on_run_end`, `on_tool_call`, `on_tool_result` callbacks |
| `kernel/context/` | `ModelContext` ABC | When building a new message-windowing or summarisation strategy |

---

## How to Add a New Feature

Never edit the kernel to add capability. The kernel grows only when a new *contract* is needed — a new Protocol or ABC that extensions must satisfy.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
flowchart TD
    Q{What do\nyou need?}

    Q -->|New agent behaviour| E1["extensions/agents/<name>/agent.py\n@register_agent('name')"]
    Q -->|New guardrail| E2["extensions/guardrails/<name>.py\n@register_guardrail('name')"]
    Q -->|New middleware| E3["extensions/middleware/<name>.py\n@register_middleware('name')"]
    Q -->|New LLM provider| E4["integrations/llm/<provider>/\nsubclass BaseModelClient"]
    Q -->|New memory backend| E5["integrations/memory/<backend>.py\nsubclass BaseMemory"]
    Q -->|New context strategy| E6["extensions/context/<name>.py\n@register_context('name')"]
    Q -->|New tool| E7["catalog/tools/<name>/tool.py\nsubclass BaseTool"]
    Q -->|New plugin category| E8["kernel/plugin/registry.py\n_make_decorator('category', base=YourABC)"]
    Q -->|Truly new contract| E9["kernel/ — add ABC / Protocol / dataclass\nno implementation, no I/O"]

    style Q fill:#7c3aed,stroke:#a78bfa,color:#f5f3ff
    style E9 fill:#991b1b,stroke:#f87171,color:#fff1f2
```

The rule is: if you can build it in `extensions/` by implementing an existing kernel ABC, do that. If you need a new ABC that doesn't exist yet, that's the one case where the kernel grows — but only the contract, never the implementation.
