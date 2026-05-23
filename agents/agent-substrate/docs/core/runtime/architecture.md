# Modular Actor Runtime — Under the Hood Architecture

This document provides a deep, comprehensive overview of the core modular actor runtime, visualising its internal structures, class hierarchies, and execution flows with **Mermaid diagrams**. 

Trust this as the ultimate developer reference for understanding how message routing, mailbox queues, supervision, P2P peer messaging, sagas, and locking operate under the hood.

---

## 0. Two-Layer Architecture

The framework is built in two clean layers. **Layer 1** is the raw actor runtime (mailboxes, dispatcher, envelopes). **Layer 2** is the declarative agent API built on top.

```mermaid
graph BT
    subgraph Layer2 ["Layer 2 — Agent Framework"]
        RA[RuntimeAgent]
        SA[SentimentAgent]
        MA[ModeratorAgent]
        UA[UserProxyAgent]
        
        SA -->|extends| RA
        MA -->|extends| RA
        UA -->|extends| RA
    end
    
    subgraph Layer1 ["Layer 1 — Core Runtime"]
        LR[LocalRuntime]
        D[Dispatcher]
        MB[Mailbox]
        SV[Supervisor]
    end
    
    RA -->|"start() → register + subscribe"| LR
    RA -->|"send() → send_message()"| LR
    RA -->|"publish() → publish_message()"| LR
    
    classDef l2 fill:#8E44AD,stroke:#6C3483,stroke-width:2px,color:#FFF;
    classDef l1 fill:#2C3E50,stroke:#1A252F,stroke-width:2px,color:#ECF0F1;
    class RA,SA,MA,UA l2;
    class LR,D,MB,SV l1;
```

| Aspect | Layer 1 (Core Runtime) | Layer 2 (RuntimeAgent) |
|---|---|---|
| Registration | `runtime.register("name", handler_fn)` | `agent.start()` (auto) |
| Subscriptions | `runtime.subscribe("name", topic)` | `subscriptions=[topic]` in constructor |
| Message handling | Bare async function | Override `on_message()` method |
| Sending messages | `runtime.send_message(msg, sender=..., recipient=...)` | `self.send(msg, recipient=...)` |
| Publishing events | `runtime.publish_message(msg, sender=..., topic=...)` | `self.publish(msg, topic=...)` |

### 0.1 RuntimeAssistantAgent Cognitive Loop

The `RuntimeAssistantAgent` is a high-level cognitive agent implemented directly on top of the Layer 2 `RuntimeAgent` contract. It provides an autonomous reasoning and tool execution loop (similar to AutoGen's AssistantAgent) with built-in guardrails:

```mermaid
flowchart TD
    In([Message Received]) --> IG{Input Guardrails?}
    IG -->|Yes| CIG[Check Input Guardrails]
    IG -->|No| AM[Append Msg to Memory]
    CIG -->|Passed| AM
    CIG -->|Blocked| RetErr([Return Guardrail Blocked])
    
    AM --> BuildCtx[Build Model Context]
    BuildCtx --> CallLLM[Generate thought/completion]
    CallLLM --> HasTools{Has Tool Calls?}
    
    HasTools -->|No| OG{Output Guardrails?}
    HasTools -->|Yes| ExTools[Execute Tools in Parallel]
    
    ExTools --> AppRes[Append Tool Results to Memory]
    AppRes --> BuildCtx
    
    OG -->|Yes| COG[Check Output Guardrails]
    OG -->|No| RetAns([Return Final Response])
    COG -->|Passed| RetAns
    COG -->|Blocked| RetErr
```

---

## 1. Actor Model Lifecycle & Component Hierarchy

The modular runtime is built on an **Actor-based architecture**. Instead of calling agent functions directly, all agents run as isolated actor loops processing incoming strictly-typed envelopes from their dedicated mailboxes.

### Core Architecture Diagram
This diagram shows how `LocalRuntime` organizes, supervises, and routes messages using the `Dispatcher`, `Mailbox`, and `Supervisor` hierarchies.

```mermaid
graph TD
    subgraph LocalRuntime ["LocalRuntime Engine"]
        R[LocalRuntime] -->|Owns| D[Dispatcher]
        R -->|Manages| S[Supervisor]
        R -->|Orchestrates| L[ResourceLockManager]
        R -->|Tracks| Sa[SagaCoordinator]
        R -->|Persists| C[CheckpointStore]
    end

    subgraph DispatcherRouting ["Dispatcher Routing Table"]
        D -->|Routes to| M1[Mailbox: worker/instance-1]
        D -->|Routes to| M2[Mailbox: worker/instance-2]
        D -->|Routes to| M3[Mailbox: tech_support/main]
        D -->|Maintains| Sub[Pub/Sub Topic Registry]
    end

    subgraph ActorLoops ["Actor Execution Context"]
        S -->|Supervises| A1[Agent Loop 1]
        S -->|Supervises| A2[Agent Loop 2]
        S -->|Supervises| A3[Agent Loop 3]
        
        M1 <.->|Pulls messages| A1
        M2 <.->|Pulls messages| A2
        M3 <.->|Pulls messages| A3
    end

    classDef primary fill:#2C3E50,stroke:#34495E,stroke-width:2px,color:#ECF0F1;
    classDef secondary fill:#18BC9C,stroke:#16A085,stroke-width:2px,color:#FFF;
    classDef storage fill:#F39C12,stroke:#D35400,stroke-width:2px,color:#FFF;
    class R,D,S primary;
    class M1,M2,M3,A1,A2,A3 secondary;
    class L,Sa,C,Sub,CheckpointStore storage;
```

---

## 2. End-to-End P2P Messaging Pipeline

When an agent or user invokes `send_message()`, the request goes through an optimized asynchronous delivery pipeline. Let's trace how a message is routed, queued, processed, and replied to.

### Flowchart: Message Dispatch and Execution Loop
```mermaid
sequenceDiagram
    autonumber
    actor User as User / Calling Agent
    participant Runtime as LocalRuntime
    participant Disp as Dispatcher
    participant Mbox as Mailbox
    participant AgentLoop as Agent Loop Task
    participant Handler as Agent Handler Function

    User->>Runtime: send_message(msg_text, sender, recipient)
    Note over Runtime: 1. Coerce content to list[TextBlock]<br/>2. Construct Envelope with Correlation ID
    Runtime->>Disp: dispatch(envelope)
    
    Note over Disp: Look up recipient Mailbox
    Disp->>Mbox: put(envelope)
    
    alt Mailbox is Full
        Mbox-->>Runtime: Raise MailboxFullError
    else Mailbox has Space
        Mbox->>Mbox: Enqueue Envelope & trigger Event
    end

    AgentLoop->>Mbox: get() (waits asynchronously)
    Mbox-->>AgentLoop: Yields Envelope
    
    Note over AgentLoop: Construct MessageContext<br/>with runtime P2P reference
    AgentLoop->>Handler: Invoke callback(ctx, content)
    activate Handler
    Handler-->>AgentLoop: Return response object (or raise Exception)
    deactivate Handler

    alt Execution Successful
        AgentLoop->>Runtime: Resolve pending response Future
        Runtime-->>User: Return value
    else Exception Raised
        AgentLoop->>Runtime: Fail pending Future with HandlerError
        Runtime-->>User: Propagate HandlerError
    end
```

---

## 3. Resilience, Supervision & Recovery

Supervisors act as safety nets. If an agent's processing loop encounters a crash or persistent failures, the `Supervisor` automatically acts according to the `RestartPolicy` (e.g. `OneForOne` or `AllForOne`), maintaining stable, long-running agent execution.

### Supervision Crash & Restart Loop
```mermaid
stateDiagram-v2
    [*] --> Active : Agent registered and running
    
    state Active {
        [*] --> Idle
        Idle --> Processing : Envelope arrives
        Processing --> Idle : Success
        Processing --> Crashed : Exception Raised
    }

    Crashed --> SupervisorNotify : Capture crash
    
    state SupervisorNotify {
        [*] --> CheckPolicy
        CheckPolicy --> StopAgent : RestartPolicy = NEVER
        CheckPolicy --> RestartAgent : RestartPolicy = ON_FAILURE
    }

    RestartAgent --> ReinitMailbox : Reset queues / states
    ReinitMailbox --> Active : Respawn agent loop task
    StopAgent --> [*] : Terminate agent registration
```

---

## 4. Saga Coordination (Fault-Tolerant Workflows)

For complex multi-agent orchestrations where multiple actions need to succeed together (e.g. Booking a trip involving flight, hotel, and car agents), a `SagaCoordinator` manages compensating tools to achieve eventual consistency.

### Saga Success vs. Compensation Path
```mermaid
graph TD
    Start([Start Saga Transaction]) --> Step1[Step 1: Book Flight]
    
    Step1 -->|Success| Step2[Step 2: Book Hotel]
    Step1 -->|Failure| Comp1[Compensate 1: Cancel Flight]
    
    Step2 -->|Success| Step3[Step 3: Book Rental Car]
    Step2 -->|Failure| Comp2[Compensate 2: Cancel Hotel]
    
    Step3 -->|Success| EndSuccess([Saga Completed Successfully])
    Step3 -->|Failure| Comp3[Compensate 3: Cancel Car]

    Comp3 --> Comp2
    Comp2 --> Comp1
    Comp1 --> EndFailure([Saga Aborted & Cleaned Up])
    
    classDef success fill:#2ECC71,stroke:#27AE60,stroke-width:2px,color:#FFF;
    classDef failure fill:#E74C3C,stroke:#C0392B,stroke-width:2px,color:#FFF;
    classDef step fill:#3498DB,stroke:#2980B9,stroke-width:2px,color:#FFF;
    class Start,EndSuccess success;
    class EndFailure failure;
    class Step1,Step2,Step3,Comp1,Comp2,Comp3 step;
```

---

## 5. Resource Locking with Deadlock Protection

To prevent concurrent agents from modifying the same data or calling the same tools simultaneously, the `ResourceLockManager` implements non-blocking asynchronous key locks with safety timeout triggers.

```mermaid
graph TD
    A[Agent 1 Request Lock: key_abc] --> B{Is key_abc locked?}
    B -->|No| C[Acquire lock + set TTL timeout]
    B -->|Yes| D{Does requester own it?}
    
    D -->|Yes| E[Re-entrant access granted]
    D -->|No| F[Wait / Poll queue with timeout]
    
    F -->|Timeout Exceeded| G[Raise LockTimeoutError / Deadlock Protection]
    F -->|Released by Owner| C
    
    classDef lockGreen fill:#2ECC71,stroke:#27AE60,stroke-width:2px,color:#FFF;
    classDef lockRed fill:#E74C3C,stroke:#C0392B,stroke-width:2px,color:#FFF;
    classDef lockBlue fill:#3498DB,stroke:#2980B9,stroke-width:2px,color:#FFF;
    class C,E lockGreen;
    class G lockRed;
    class A,B,D,F lockBlue;
```
