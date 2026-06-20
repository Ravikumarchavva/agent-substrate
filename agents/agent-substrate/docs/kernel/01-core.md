# core/ — The Universal Primitives

> **Source:** `kernel/core/content.py` · `kernel/core/identity.py` · `kernel/core/usage.py` · `kernel/core/errors.py`

Every agent message, every tool result, and every event payload in the entire system is built from these four files. Nothing in the kernel imports from outside `core/` except within `core/` itself.

---

## ContentBlock — The Universal Payload Primitive

Every list of data passed between agents, to the LLM, and back from tools is a `list[ContentBlock]`. There is no other payload type. The `type` literal field is the discriminator — Pydantic routes deserialization automatically.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','secondaryColor': '#E3F2FD','tertiaryColor': '#FFF3E0','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TB
    classDef msg fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef union fill:#F3E5F5,stroke:#6A1B9A,stroke-width:2px,color:#4A148C,font-weight:bold
    classDef textblk fill:#E8F5E9,stroke:#2E7D32,stroke-width:1px,color:#1B5E20
    classDef mediablk fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C
    classDef agblk fill:#FCE4EC,stroke:#880E4F,stroke-width:1px,color:#880E4F

    CHAT["ChatMessage\nrole: str\ncontent: list[ContentBlock]\nname: str | None"]:::msg
    CB["ContentBlock\ndiscriminated union\n12 types · all frozen"]:::union

    CHAT -->|"wraps"| CB

    subgraph TextData["Text & Data"]
        T["TextBlock\ntype='text'\ntext: str"]:::textblk
        CO["CodeBlock\ntype='code'\ncode · language"]:::textblk
        DA["DataBlock\ntype='data'\ndata: dict · schema_id"]:::textblk
        ER["ErrorBlock\ntype='error'\nerror_type · recoverable: bool"]:::textblk
    end

    subgraph MediaBlocks["Media — URL or bytes or file_id"]
        IM["ImageBlock\ntype='image'\nurl | data | file_id"]:::mediablk
        AU["AudioBlock\ntype='audio'\ntranscript: str | None"]:::mediablk
        VI["VideoBlock\ntype='video'"]:::mediablk
        DO["DocumentBlock\ntype='document'\nfilename: str | None"]:::mediablk
    end

    subgraph AgentBlocks["Agentic"]
        TU["ToolUseBlock\ntype='tool_use'\ncall_id · tool_name · args"]:::agblk
        TR["ToolResultBlock\ntype='tool_result'\ncall_id · content · is_error"]:::agblk
        TH["ThinkingBlock\ntype='thinking'\nredacted: bool"]:::agblk
        UI["UIResourceBlock\ntype='ui_resource'\nuri · render: inline|panel|fullscreen"]:::agblk
    end

    CB --> TextData
    CB --> MediaBlocks
    CB --> AgentBlocks
```

### Key points

- **All blocks are frozen Pydantic models.** They are immutable once created.
- **`ToolResultBlock.content` is itself a `list[ContentBlock]`** — a tool can return images, code, data, errors, or any mix.
- **`ThinkingBlock`** stores extended chain-of-thought from Anthropic extended thinking. The `redacted` flag lets the UI hide raw reasoning from users.
- **`UIResourceBlock`** is the narrow waist for interactive UIs. A tool emits a `ui://name` URI; `ravi-ui` renders it as a sandboxed iframe. The LLM only sees `text` — never the iframe.
- **`ErrorBlock`** over `TextBlock` — use `ErrorBlock` when a tool fails so consumers can detect errors programmatically without string-matching.
- **`register_block_type(cls)`** extends the discriminator registry for custom block types. Call once at module load time.
- **`content_block_from_dict(data)`** deserializes raw dicts. Unknown `type` values become `UnknownBlock` — no data loss in mixed-version deployments.

---

## AgentId and TopicId — Routing Addresses

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph LR
    classDef id fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E,font-weight:bold
    classDef example fill:#E3F2FD,stroke:#1565C0,stroke-width:1px,color:#0D47A1,font-style:italic

    AID["AgentId\ntype: str\nkey: str\nnamespace: str = 'default'\n\nstr → type/key\nor namespace/type/key"]:::id
    TID["TopicId\ntype: str\nsource: str = 'default'\nnamespace: str = 'default'\n\nstr → type/source\nor namespace/type/source"]:::id

    AIDEX["AgentId('react', 'sess-123')\n→ react/sess-123"]:::example
    TIDEX["TopicId('agent.progress', run_id)\n→ agent.progress/run-abc"]:::example

    AID --- AIDEX
    TID --- TIDEX
```

`AgentId` is the **point-to-point** routing key — send a message to one specific agent instance.

`TopicId` is the **pub/sub** routing key — emit on a topic; every follower gets the message.

Standard topic conventions (enforced at L1, not kernel):

| Topic | Purpose |
|---|---|
| `agent.progress / {run_id}` | All progress events for one run tree — one subscription covers the whole tree |
| `agent.stream / {agent_id.key}` | Token stream from one specific agent |

---

## Usage — Token Accounting

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph LR
    classDef field fill:#E8EAF6,stroke:#3949AB,stroke-width:1px,color:#1A237E
    classDef note fill:#FFFDE7,stroke:#F57F17,stroke-width:1px,color:#E65100,font-style:italic

    U["Usage (frozen dataclass)\ninput_tokens: int\ncached_tokens: int\noutput_tokens: int\nreasoning_tokens: int\ntotal_tokens: int (property)"]:::field

    N1["cached_tokens ⊂ input_tokens\nbilled at a lower rate"]:::note
    N2["reasoning_tokens ⊂ output_tokens\nextended thinking only"]:::note
    N3["Usage + Usage = Usage\nadditive across LLM calls"]:::note

    U --- N1
    U --- N2
    U --- N3
```

`Usage` is additive (`a + b` works). Every `LLMResponse` carries one. The L1 `ExecutionTracker` accumulates them to enforce `ExecutionBudget.max_tokens`.

---

## KernelErrors — Typed Runtime Failures

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#FFEBEE','primaryBorderColor': '#C62828','primaryTextColor': '#B71C1C','lineColor': '#C62828','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TB
    classDef base fill:#FFEBEE,stroke:#C62828,stroke-width:2px,color:#B71C1C,font-weight:bold
    classDef err fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C
    classDef policy fill:#FCE4EC,stroke:#880E4F,stroke-width:1px,color:#880E4F
    classDef concur fill:#F3E5F5,stroke:#6A1B9A,stroke-width:1px,color:#4A148C

    KE["KernelError\n(base — catch-all)"]:::base

    ANF["AgentNotFoundError\nSent to unregistered AgentId\n+ agent_id: AgentId"]:::err
    HE["HandlerError\nMessage handler raised internally\n+ cause: Exception"]:::err
    ACE["AgentCrashError\nUnexpected run failure\n+ run_id · agent_id"]:::err
    BEE["BudgetExhaustedError\nHeadcount or token limit hit"]:::policy
    MT["MiddlewareTermination\nIntentional policy halt\n(guardrail, rate limit)\n+ message: str"]:::policy
    CE["CancellationError\nCooperative cancel or deadline"]:::policy
    CAE["ConcurrentAppendError\nTwo workers wrote same run\n+ expected_seq · actual_seq"]:::concur
    SD["SpawnDenied\nSpawnBudget exhausted\n+ parent_run · budget"]:::concur

    KE --> ANF
    KE --> HE
    KE --> ACE
    KE --> BEE
    KE --> MT
    KE --> CE
    KE --> CAE
    KE --> SD
```

`AgentCrashError` — the orchestrator catches this, consults the retry policy, and re-dispatches the crashed agent from the last checkpoint.

`MiddlewareTermination` vs `AgentCrashError` — termination is **intentional** (policy blocked the run); crash is **unexpected** (agent code threw).

`ConcurrentAppendError` — two workers tried to write to the same `EventLog` simultaneously. Callers reload `last_seq` and retry. This fences concurrent writes without a distributed lock.
