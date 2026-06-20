# agent/ — What an Agent IS

> **Source:** `kernel/agent/context.py` · `kernel/agent/middleware.py` · `kernel/agent/runtime_context.py` · `kernel/agent/supervision.py` · `kernel/runtime/agent.py`

Defines the `Agent` Protocol, the supervision tree that governs it, the execution context threaded through every call, and the three-level middleware chain that wraps every operation.

---

## The Agent Protocol

The simplest possible contract — just an identity and an entry point:

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TB
    classDef protocol fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef impl fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E
    classDef ctx fill:#E8F5E9,stroke:#2E7D32,stroke-width:1px,color:#1B5E20
    classDef msg fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C

    AGT["Agent (Protocol)\n@runtime_checkable\nid: AgentId\nrun(ctx, inbox) → None"]:::protocol

    REACT["ReActAgent\n(L1 — agents/core/)"]:::impl
    ORCH["OrchestratorAgent\n(L1 — agents/core/)"]:::impl
    INFO["InformationAgent\n(L1 — agents/core/)"]:::impl
    CUSTOM["YourCustomAgent\n(anywhere — just implement id + run)"]:::impl

    CTX["AgentRunContext (Protocol)\nkernel-visible slice of RunContext\nrun_id: str\ntenant_id: str | None\ncheck()"]:::ctx
    INBOX["list[Message]\ndrained from Inbox\nfor this wake cycle"]:::msg

    AGT -->|"called with"| CTX
    AGT -->|"called with"| INBOX
    REACT -.->|"implements"| AGT
    ORCH -.->|"implements"| AGT
    INFO -.->|"implements"| AGT
    CUSTOM -.->|"implements"| AGT
```

`AgentRunContext` is the **kernel-visible slice** only. At runtime, `ctx` is actually a `RunContext` from L1, which adds all the journaled methods: `ctx.llm()`, `ctx.tool()`, `ctx.spawn()`, `ctx.join()`, `ctx.emit()`, `ctx.sleep_until()`. Type-hint with `RunContext` in your agent code for full IDE support.

---

## Supervision — The Agent Org-Chart

When an orchestrator spawns a sub-agent, it passes `Supervision` — the child's formal position in the execution tree.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TB
    classDef sv fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef budget fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E
    classDef policy fill:#E8F5E9,stroke:#2E7D32,stroke-width:1px,color:#1B5E20
    classDef enum fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C

    SV["Supervision (frozen)\nrun_id: str\nsession_id: str\nroot_id: AgentId\nparent_id: AgentId | None\ndepth: int\nspawn_budget: SpawnBudget\nexecution_budget: ExecutionBudget\nretention: HistoryRetention\npriority: Priority"]:::sv

    SB["SpawnBudget (frozen)\nmax_agents: int = 50\nallow_preempt: bool = True\n\nShared tree-wide —\nsame object in every node"]:::budget

    EB["ExecutionBudget (frozen)\nmax_tokens: int | None\nmax_cost_usd: float | None\nmax_turns: int | None\ndeadline_s: float | None\n\nNone = unlimited"]:::budget

    HR["HistoryRetention\nNONE — stateless worker\nRUN — kept for this run\nPERMANENT — kept forever"]:::enum

    PRI["Priority (int weights)\nBACKGROUND = 0\nLOW = 1\nNORMAL = 2\nHIGH = 4\nCRITICAL = 8"]:::enum

    SV --> SB
    SV --> EB
    SV --> HR
    SV --> PRI
```

### How it propagates down the tree

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TD
    classDef root fill:#1565C0,stroke:#0D47A1,stroke-width:2px,color:#FFFFFF,font-weight:bold
    classDef child fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E
    classDef info fill:#FFFDE7,stroke:#F57F17,stroke-width:1px,color:#E65100,font-style:italic

    ROOT["Root Agent\nSupervision.root(agent_id)\nrun_id = NEW UUID\nsession_id = NEW UUID\nparent_id = None\ndepth = 0\nretention = PERMANENT"]:::root

    C1["Child A\nspawn_child(parent_id=root)\nrun_id = SAME\nsession_id = SAME\nparent_id = root\ndepth = 1\nretention = RUN"]:::child

    C2["Child B\nspawn_child(parent_id=root)\nrun_id = SAME\nsession_id = SAME\nparent_id = root\ndepth = 1\nretention = RUN"]:::child

    GC["Grandchild\nspawn_child(parent_id=childA)\nrun_id = SAME\nsession_id = SAME\nparent_id = childA\ndepth = 2"]:::child

    SBI["SpawnBudget: max_agents=50\nSHARED — same object\nacross the whole tree"]:::info

    ROOT --> C1
    ROOT --> C2
    C1 --> GC
    ROOT -.- SBI
    C1 -.- SBI
    C2 -.- SBI
    GC -.- SBI
```

`session_id` and `run_id` serve different scopes:
- `session_id` — the conversation thread. Long-lived, many runs. **History is keyed by this.**
- `run_id` — one execution tree. Short-lived, one `run()` call. **Budget, supervision, and progress topic are keyed by this.**

---

## RunMeta and CancellationToken

Threaded through every kernel API call — LLM calls, tool executions, storage reads. Lets any operation be cancelled cooperatively without global state.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TB
    classDef rm fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef ct fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E
    classDef method fill:#E8F5E9,stroke:#2E7D32,stroke-width:1px,color:#1B5E20
    classDef note fill:#FFFDE7,stroke:#F57F17,stroke-width:1px,color:#E65100,font-style:italic

    RM["RunMeta (frozen)\nrun_id: str\ncancellation: CancellationToken\nsupervision: Supervision | None\ndeadline: datetime | None\ntrace_id: str\ntenant_id: str | None"]:::rm

    CT["CancellationToken\n_cancelled: bool\n_event: asyncio.Event"]:::ct

    CANCEL["cancel(reason)\nIdempotent — safe to call multiple times\nFires all registered callbacks"]:::method
    CHECK["check()\nRaises CancellationError\nif cancelled or deadline past\n\nCall at every yield point"]:::method
    WAIT["await wait()\nBlocks until token is cancelled"]:::method
    CHILD["child()\nReturns a child token\nParent cancel → child cancel\nChild cancel ≠ parent cancel"]:::method

    RM --> CT
    CT --> CANCEL
    CT --> CHECK
    CT --> WAIT
    CT --> CHILD

    N1["RunMeta.check() = cancellation.check() + deadline check"]:::note
    N2["child token: cancelling parent cascades down\ncancelling child does NOT cancel parent"]:::note
    RM -.- N1
    CHILD -.- N2
```

`RunMeta` is immutable. Thread it down call stacks. For child spans: create a new `RunMeta` with a child token (cancellation propagates) and a new trace span.

---

## Three-Level Middleware Pipeline

Every operation passes through three nested interceptor chains — one per level of the agent loop.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TB
    classDef pipe fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef mw fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E
    classDef target fill:#E8F5E9,stroke:#2E7D32,stroke-width:1.5px,color:#1B5E20,font-weight:bold
    classDef ctx fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C
    classDef example fill:#F3E5F5,stroke:#6A1B9A,stroke-width:1px,color:#4A148C,font-style:italic

    Request["User Request"]:::target

    subgraph L1["AgentMiddleware — wraps agent.run()"]
        AM1["RateLimiter"]:::mw
        AM2["AuditLogger"]:::mw
        AM3["SessionGuard"]:::mw
        ARC["AgentRunContext\nagent_name · run_id · session_id"]:::ctx
    end

    subgraph L2["ChatMiddleware — wraps model.generate()"]
        CM1["ContentGuardrail"]:::mw
        CM2["PIIFilter"]:::mw
        CC["ChatContext\nagent_name · run_id\nsystem_instructions"]:::ctx
    end

    subgraph L3["FunctionMiddleware — wraps tool.execute()"]
        FM1["ApprovalGate"]:::mw
        FM2["ToolAuditLogger"]:::mw
        FC["FunctionContext\nagent_name · run_id\nfunction_name · arguments"]:::ctx
    end

    AGENTRUN["agent.run()"]:::target
    LLMCALL["model.generate()"]:::target
    TOOLCALL["tool.execute()"]:::target

    Request --> L1
    L1 --> AGENTRUN
    AGENTRUN --> L2
    L2 --> LLMCALL
    LLMCALL --> L3
    L3 --> TOOLCALL

    HALT["MiddlewareTermination\nraises to halt at any level"]:::example
    AM1 -.- HALT
    CM1 -.- HALT
    FM1 -.- HALT
```

All three levels share one Protocol shape:

```python
class Middleware(Protocol[CtxT]):
    async def process(self, context: CtxT,
                      call_next: Callable[[], Awaitable[None]]) -> None: ...
```

Call `call_next()` to continue; raise `MiddlewareTermination` to halt. The concrete `MiddlewarePipeline` at L1 chains registered instances.

---

## AgentContextProtocol — What the Loop Reads

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph LR
    classDef proto fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef impl fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E
    classDef strat fill:#E8F5E9,stroke:#2E7D32,stroke-width:1px,color:#1B5E20

    ACP["AgentContextProtocol\nagent_id: AgentId\nget_prompt_window(session_id)"]:::proto

    AC["AgentContext\n(L1 — agents/context/)"]:::impl
    HP["HistoryProvider\nraw message transcript"]:::strat
    CS["CompactionStrategy\nconverts history to LLM window\nsliding window | truncation | summarisation"]:::strat

    AC -.->|"implements"| ACP
    AC --> HP
    AC --> CS
```

`get_prompt_window(session_id)` is the only thing the agent loop calls on context. Internally it calls the `HistoryProvider` to get the transcript and the `CompactionStrategy` to reduce it to a manageable LLM context window.
