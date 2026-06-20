# messaging/ — Agent Communication

> **Source:** `kernel/messaging/message.py` · `kernel/messaging/stream.py` · `kernel/messaging/events.py`

Three distinct communication channels — messages between agents, stream events to the UI, and generic events to the event bus. They look similar but serve different purposes.

---

## The Message — Agent-to-Agent Envelope

A `Message` wraps any `Payload` and routes it to a specific agent or topic. Every agent-to-agent send goes through a `Message`.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TB
    classDef envelope fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef payload fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E,font-weight:bold
    classDef builtin fill:#E8F5E9,stroke:#2E7D32,stroke-width:1px,color:#1B5E20
    classDef routing fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C

    MSG["Message\nid: str (UUID hex)\ntarget: AgentId | TopicId\nsender: AgentId | None\ncorrelation_id: str\ncausation_id: str | None\nreply_to: str | None\nis_broadcast: bool"]:::envelope

    PB["PayloadBase\n(abstract)\nkind: str"]:::payload

    subgraph BuiltIn["Built-in Payload Types"]
        CP["ChatPayload\nkind='chat'\nmessage: ChatMessage"]:::builtin
        DP["DataPayload\nkind='data'\ndata: dict"]:::builtin
        CTL["ControlPayload\nkind='control'\nsignal: str · data: dict"]:::builtin
        PP["ProgressPayload\nkind='progress'\nprogress: AgentProgress"]:::builtin
        TCR["ToolCallRequest\nkind='tool_call'\nname · arguments · call_id"]:::builtin
        TER["ToolExecutionResult\nkind='tool_result'\ncall_id · content · is_error"]:::builtin
    end

    ROUTE["target routing\nAgentId → point-to-point\nTopicId → pub/sub fan-out"]:::routing

    MSG -->|"wraps"| PB
    PB --> CP
    PB --> DP
    PB --> CTL
    PB --> PP
    PB --> TCR
    PB --> TER
    MSG --> ROUTE
```

**Key fields explained:**

| Field | Purpose |
|---|---|
| `correlation_id` | Ties all messages in one logical conversation/run together |
| `causation_id` | Points to the specific message that triggered this one — builds a causal chain |
| `reply_to` | The `run_id` of the asker — set by `RunContext.ask()` so the responder knows where to send the reply |
| `is_broadcast` | `True` when `target` is a `TopicId` — triggers fan-out delivery to all followers |

**`register_payload_type(cls)`** — add custom payload kinds. `cls` must subclass `PayloadBase` and have a `kind: Literal[...]` field. Call once at module load time.

---

## Two Parallel Visibility Streams

While agents run, two independent channels stream to the UI. They share `seq` for ordering but serve different consumers.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'actorBkg': '#E8EAF6','actorBorder': '#3949AB','actorTextColor': '#1A237E','activationBkgColor': '#E3F2FD','activationBorderColor': '#1565C0','noteBkgColor': '#FFFDE7','noteBorderColor': '#F57F17','signalColor': '#546E7A','signalTextColor': '#263238','fontSize': '13px'}}}%%
sequenceDiagram
    autonumber
    participant Root as "Root Agent"
    participant Sub as "Sub-Agent"
    participant UI as "ravi-ui Client"

    Note over Root,UI: One run, one progress topic: TopicId("agent.progress", run_id)
    Note over Root,UI: Each agent has its own token stream: TopicId("agent.stream", agent_id.key)

    Root->>UI: AgentProgress(step=started, depth=0)
    Root->>UI: AgentProgress(step=thinking, depth=0)

    Root->>Sub: spawn child
    Sub->>UI: AgentProgress(step=started, parent_id=root, depth=1)

    loop Sub-agent token stream
        Sub->>UI: TextDelta(text, agent_id=sub, seq)
    end

    Sub->>UI: CompletionEvent(content, usage, seq)
    Sub->>UI: AgentProgress(step=done, depth=1)

    Root->>UI: AgentProgress(step=done, depth=0)

    Note over UI: UI subscribes ONCE to agent.progress/run_id<br/>Reconstructs tree from agent_id+parent_id+depth
```

### Stream event types

**Token stream** — `TopicId("agent.stream", agent_id.key)` — one topic per agent:

| Type | When | Key fields |
|---|---|---|
| `TextDelta` | Each text token from the LLM | `text`, `seq`, `agent_id`, `run_id` |
| `ReasoningDelta` | Each thinking token (extended-thinking only) | `text`, `seq` |
| `CompletionEvent` | End of LLM call | `content: list[ContentBlock]`, `usage`, `seq` |
| `StreamDone` | End sentinel | `reason: str` |

**Progress stream** — `TopicId("agent.progress", run_id)` — ONE topic for the whole run:

| `AgentStep` | Meaning |
|---|---|
| `started` | Agent woke up, beginning `run()` |
| `thinking` | Agent made an LLM call |
| `tool_call` | Agent invoked a tool |
| `tool_result` | Tool returned a result |
| `handoff` | Orchestrator delegated to a sub-agent |
| `paused` | Agent suspended (waiting for signal/timer/child) |
| `done` | Agent completed |
| `error` | Agent failed |

`AgentProgress.depth` is used by the UI to indent sub-agents correctly in the tree view.

---

## Event — The Generic Bus Envelope

Separate from `Message`. `Event` is the envelope for the Redis pub/sub event bus (`integrations/events/`). It carries infrastructure-level events like `workflow.started`, `agent.crashed`, `hitl.approved`.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph LR
    classDef ev fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E
    classDef proto fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef impl fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C,stroke-dasharray:4 2

    EV["Event (frozen)\nid: str\ntype: str\nsource: str\nschema_version: int\ncorrelation_id: str\nts: datetime\ndata: dict"]:::ev

    PUB["EventPublisher\n(Protocol)\npublish(event, topic)"]:::proto
    SUB["EventSubscriber\n(Protocol)\nsubscribe(topic, handler)\nunsubscribe(id)\nstream(topic)"]:::proto

    REDIS["RedisEventBus\n(integrations/events/)"]:::impl
    INPROC["InProcessEventBus\n(serving/monolith/sse/)"]:::impl

    EV --> PUB
    EV --> SUB
    REDIS -.->|"implements"| PUB
    REDIS -.->|"implements"| SUB
    INPROC -.->|"implements"| PUB
    INPROC -.->|"implements"| SUB
```

**Always use factory functions** from `serving/shared/events/types.py` — never construct `Event` dicts manually:

```python
from ravi.serving.shared.events.types import workflow_started
await bus.publish(workflow_started(run_id=run.id, thread_id=thread.id, user_content=text))
```

`Event.create()` is the kernel-level convenience constructor for when factory functions don't exist yet.
