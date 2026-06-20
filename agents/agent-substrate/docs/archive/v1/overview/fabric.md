# L1 · The Fabric

The **Fabric** layer is the operating system and messaging middleware of the Ravi framework. It provides the actor-model implementation, manages message dispatch and fan-out, coordinates transactions, and supervises agent lifecycle states across local and distributed topologies.

---

## The Actor: Every Agent is a Node

In Ravi, every active agent is an actor extending the `ActorAgent` abstract base class. Individual agents do not run as isolated procedures. Instead, they exist as nodes inside a runtime system.

### Key Characteristics of ActorAgent
*   **Decoupled & Addressable**: Communicates strictly using `AgentId` coordinates. An agent does not hold references to other agent instances directly.
*   **Unified Entry Point**: All work enters through the async `on_message(ctx, content)` interface.
*   **Outward-Only Communication**: Agents emit results and trigger downstream steps using `send(msg, recipient)` (point-to-point) or `publish(msg, topic)` (event-driven broadcast).

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569"}}}%%
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

    class AssistantAgent {
        +on_message(ctx, content) async
        -_run_impl(text) async
    }

    class UserProxyAgent {
        +ask(text, recipient) async
        +on_message(ctx, content) async
    }

    class OrchestratorAgent {
        +sub_agents: list
        +on_message(ctx, content) async
    }

    ActorAgent <|-- AssistantAgent : extends
    ActorAgent <|-- UserProxyAgent : extends
    ActorAgent <|-- OrchestratorAgent : extends
    AssistantAgent ..> StreamChannel : streams to
```

---

## Runtime Message Flow Architecture

Ravi supports both `LocalRuntime` (in-process queue orchestration) and `DistributedRuntime` (Redis/gRPC backplane). The rest of the framework interacts strictly with the `AgentRuntime` interface, allowing developers to scale their topologies without changing their agent code.

### 1. Point-to-Point Messaging (`send_message`)
Point-to-point delivery uses a target mailbox architecture to isolate and buffer execution. The runtime handles routing middleware, lazy agent activation, queueing, and response matching.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#475569", "edgeLabelBackground": "#1e293b"}}}%%
sequenceDiagram
    autonumber
    participant C as Sender Agent
    participant R as LocalRuntime
    participant MW as Routing Middleware
    participant D as Dispatcher
    participant MB as Mailbox (Queue)
    participant H as Actor.on_message()

    C->>R: send_message(msg, recipient=AgentId)
    R->>R: Ensure recipient agent is instantiated
    R->>R: Wrap payload inside Envelope
    R->>MW: Run pre-dispatch routing middleware
    MW-->>R: Approved / Dropped
    R->>R: Create response Future
    R->>D: dispatch(Envelope)
    D->>MB: mailbox.put(Envelope)
    MB->>H: await on_message(ctx, content)
    H-->>MB: Return Execution Result
    MB->>R: Resolve response Future
    R-->>C: Return Result Envelope
```

### 2. Event Broadcast / Pub-Sub (`publish_message`)
Event-driven workflows use fire-and-forget broadcasting. The runtime automatically resolves matching topics and fans the message out to the mailbox of every subscribed agent.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9", "primaryBorderColor": "#334155"}}}%%
flowchart LR
    subgraph Sender ["Message Producer"]
        P["publish_message<br/>topic=TopicId(...)"]
    end

    subgraph Fabric ["Runtime Core"]
        MW["Routing Middleware"]
        D["Dispatcher"]
    end

    subgraph Subscribers ["Actor Mailboxes"]
        A1["Agent A<br/>on_message()"]
        A2["Agent B<br/>on_message()"]
        A3["Agent C<br/>on_message()"]
    end

    P --> MW --> D
    D -->|fan-out| A1
    D -->|fan-out| A2
    D -->|fan-out| A3

    style P fill:#1d4ed8,stroke:#3b82f6,color:#eff6ff
    style MW fill:#7c3aed,stroke:#a78bfa,color:#f5f3ff
    style D fill:#065f46,stroke:#34d399,color:#ecfdf5
    style A1 fill:#92400e,stroke:#fbbf24,color:#fffbeb
    style A2 fill:#92400e,stroke:#fbbf24,color:#fffbeb
    style A3 fill:#92400e,stroke:#fbbf24,color:#fffbeb
```

---

## Agent Lifecycle State Machine

The runtime supervises the health, activation, and recovery states of every registered actor instance via an Erlang-style supervisor pattern.

```mermaid
%%{init: {"theme": "base", "themeVariables": {"primaryColor": "#1e293b", "primaryTextColor": "#f1f5f9"}}}%%
stateDiagram-v2
    direction LR
    [*] --> DORMANT : Agent registered
    DORMANT --> ACTIVATING : Message arrives
    ACTIVATING --> ACTIVE : Lease acquired / Queue allocated
    ACTIVE --> SUSPENDED : Unhandled exception in handler
    SUSPENDED --> ACTIVATING : Supervisor triggers restart (within threshold)
    SUSPENDED --> DORMANT : Restart limit exceeded (escalate error)
    ACTIVE --> HIBERNATING : Idle timeout / hibernate()
    HIBERNATING --> DORMANT : Lease released / Queue cleaned up
    DORMANT --> [*] : Runtime shutdowns
```

> [!TIP]
> In distributed configurations, the `ACTIVATING` phase communicates with a `LeaseRegistry`. If a lease is already held by a node in another process, the runtime fails over or proxies the message, facilitating seamless high-availability deployments.

---

## Reliability and Coordination Engines

The Fabric layer packs three crucial subsystems that ensure reliability across long-running or volatile operations:

### 1. Saga Coordinator (`SagaCoordinator`)
For operations marked as **critical**, the runtime wraps tool execution in a Saga pattern. If an actor crashes mid-transaction, the coordinator utilizes a WAL (Write-Ahead Log) stored in the `CheckpointStore` to either replay the remaining steps or apply rollback compensations using registered compensating tools.

### 2. Resource Lock Manager (`ResourceLockManager`)
An advisory locking system that secures specific resource URIs (e.g., database rows, filesystem paths) before tool execution. This prevents multiple concurrent actor steps from editing or corrupting the same external resource.

### 3. Checkpoint Store (`CheckpointStore`)
Maintains incremental snapshots of agent memories, lineage metadata, and runtime states, enabling rapid recovery after system restarts.
