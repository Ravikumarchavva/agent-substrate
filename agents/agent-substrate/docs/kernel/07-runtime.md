# runtime/ — Durable Execution Machinery

> **Source:** `kernel/runtime/ids.py` · `kernel/runtime/log_entry.py` · `kernel/runtime/effects.py` · `kernel/runtime/inbox.py` · `kernel/runtime/scheduler.py` · `kernel/runtime/supervisor.py` · `kernel/runtime/wakeup.py` · `kernel/runtime/follow_graph.py` · `kernel/runtime/fanout.py` · `kernel/runtime/communication.py`

The deepest part of the kernel. These contracts make agent runs survive crashes, scale across workers, and resume correctly after sleeping for hours — without the agent author doing anything special.

---

## Run Lifecycle

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
stateDiagram-v2
    [*] --> PENDING : enqueue()
    PENDING --> RUNNING : lease(worker_id)
    RUNNING --> SUSPENDED : release(SUSPENDED) + Wakeup stored
    SUSPENDED --> PENDING : wake_suspended() / timer / signal / child_done
    RUNNING --> COMPLETED : release(COMPLETED)
    RUNNING --> FAILED : release(FAILED)
    RUNNING --> CANCELLED : release(CANCELLED)
    FAILED --> PENDING : retry_policy.max_retries not exhausted
    FAILED --> [*] : retries exhausted → dead-run
    COMPLETED --> [*]
    CANCELLED --> [*]

    note right of SUSPENDED : Zero RAM, zero CPU\nJust rows in storage\nWaits for Wakeup
    note right of FAILED : RunRetryPolicy governs\nmax_retries and backoff_s
```

`SUSPENDED` is the efficient idle state — a run waiting for a message, timer, signal, or child completion costs nothing while suspended.

---

## The Full Execution Flow

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'actorBkg': '#E8EAF6','actorBorder': '#3949AB','actorTextColor': '#1A237E','activationBkgColor': '#E3F2FD','activationBorderColor': '#1565C0','noteBkgColor': '#FFFDE7','noteBorderColor': '#F57F17','signalColor': '#546E7A','signalTextColor': '#263238','fontSize': '13px'}}}%%
sequenceDiagram
    autonumber
    participant Client
    participant Runtime as "Runtime (L1 facade)"
    participant Inbox
    participant Scheduler
    participant Worker
    participant EventLog
    participant Agent
    participant Journal

    Client->>+Runtime: submit(agent_id, message)
    Runtime->>Inbox: deliver(agent_id, msg, notify=False)
    Runtime->>+Scheduler: register_run(run_id, agent_id)
    Runtime->>Scheduler: enqueue(run_id, priority)
    Scheduler-->>-Runtime: queued

    Runtime-->>-Client: run_id

    note over Scheduler,Worker: Worker polls for leases
    Scheduler->>+Worker: lease(run_id, agent_id)
    Worker->>+EventLog: append("run.started", seq=0)

    Worker->>Inbox: drain(agent_id)
    Inbox-->>Worker: list[Message]

    Worker->>+Agent: run(ctx, inbox)

    rect rgb(232, 234, 246)
        note over Agent,Journal: At-most-once effect via Journal
        Agent->>Journal: lookup(effect_id)
        Journal-->>Agent: None (miss)
        Agent->>Agent: execute tool / LLM call
        Agent->>Journal: record(EffectResult)
        Agent->>EventLog: append("tool.result", seq=N)
    end

    Agent->>EventLog: append("run.completed", seq=N+1)
    Agent-->>-Worker: returns None

    Worker->>Inbox: ack(msg_id) for each message
    Worker->>-Scheduler: release(lease, status=COMPLETED)
```

---

## EventLog — Source of Truth

A run's state is always `fold(all entries from seq=0)`. There is no separate state table. On crash, any worker can pick up the run, replay the log, and continue from exactly where it left off.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph LR
    classDef entry fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E
    classDef proto fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef guard fill:#FFEBEE,stroke:#C62828,stroke-width:1px,color:#B71C1C

    EL["EventLog (Protocol)\nappend(run_id, entry, expected_seq) → int\nread(run_id, from_seq) → AsyncIterator\ntail(run_id, from_seq) → AsyncIterator\nlast_seq(run_id) → int"]:::proto

    RLE["RunLogEntry (frozen)\nrun_id: RunId\nseq: int\nkind: str\npayload: dict\nts: datetime"]:::entry

    KINDS["Standard kind values\nrun.started\nmsg.received\ntool.called\ntool.result\nchild.spawned\nchild.completed\nrun.suspended\nrun.completed\nrun.failed\nrun.cancelled"]:::entry

    OCC["ConcurrentAppendError\nTwo workers wrote same run\nCaller reloads last_seq and retries\nFences concurrent writes without lock"]:::guard

    EL --> RLE
    RLE --> KINDS
    EL -.- OCC
```

**`tail()`** — live-streaming view. Never completes on its own. Used by the gateway for real-time "watch this agent" streaming and for VOD replay (`from_seq=0` replays from the beginning). Cancel the enclosing async task to stop.

---

## Journal — At-Most-Once Side-Effects

Prevents executing the same side-effect twice when a worker crashes mid-tool.

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'actorBkg': '#E8EAF6','actorBorder': '#3949AB','actorTextColor': '#1A237E','activationBkgColor': '#E3F2FD','activationBorderColor': '#1565C0','noteBkgColor': '#FFEBEE','noteBorderColor': '#C62828','signalColor': '#546E7A','signalTextColor': '#263238','fontSize': '13px'}}}%%
sequenceDiagram
    autonumber
    participant Agent
    participant Journal
    participant External as "External Service"

    Agent->>Agent: effect_id = Effect.make_id(run_id, step_seq, "email.send", args)
    Agent->>+Journal: lookup(effect_id)

    alt Cache HIT (replay path)
        Journal-->>Agent: EffectResult (cached)
        Note over Agent: Return cached result<br/>Do NOT re-execute
    else Cache MISS (first execution)
        Journal-->>-Agent: None
        Agent->>+External: send_email(to, subject, body)
        External-->>-Agent: 200 OK

        Agent->>Journal: record(EffectResult(effect_id, "ok", {msgId: ...}))
        Note over Agent,Journal: Write-once — duplicate record() is a no-op
    end
```

**The crash window:** If the worker dies after `execute()` but before `record()`, the effect may have happened without a journal entry. On replay the miss path runs again — the effect executes twice in that window. This is at-most-once's trade-off: we never double-send on record failures, but the crash window is unavoidable. Tools that are genuinely idempotent (GET calls, Stripe with idempotency key) are safe to re-run and should say so in their `description`.

`Effect.make_id()` is deterministic: SHA-256 of `{run_id, step_seq, kind, args}` with sorted keys. The same logical step in the same run always produces the same id.

---

## Inbox — Per-Agent Mailbox

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'actorBkg': '#E8EAF6','actorBorder': '#3949AB','actorTextColor': '#1A237E','activationBkgColor': '#E3F2FD','activationBorderColor': '#1565C0','noteBkgColor': '#FFFDE7','noteBorderColor': '#F57F17','signalColor': '#546E7A','signalTextColor': '#263238','fontSize': '13px'}}}%%
sequenceDiagram
    autonumber
    participant Sender
    participant Inbox
    participant Scheduler
    participant Worker

    Sender->>+Inbox: deliver(agent_id, msg, notify=True)
    Note over Inbox: Idempotent — same msg.id is a no-op
    Inbox->>Scheduler: trigger wakeup for agent
    Inbox-->>-Sender: True (delivered) / False (duplicate)

    Scheduler->>+Worker: lease(run_id)
    Worker->>+Inbox: drain(agent_id, max=100)
    Inbox-->>-Worker: list[Message] (FIFO per sender)
    Note over Worker: Process each message

    alt Success
        Worker->>Inbox: ack(agent_id, msg_id)
    else Failure
        Worker->>Inbox: nack(agent_id, msg_id, error)
        Note over Inbox: Increment retry counter
        alt retry_count < max_retries
            Inbox->>Inbox: keep in inbox
        else max_retries hit
            Inbox->>Inbox: move to dead_letters
        end
    end

    Worker-->>-Scheduler: release(lease)
```

Three robustness guarantees every implementation must honour:
1. **Idempotent delivery** — `deliver()` with the same `Message.id` twice is a no-op. At-least-once transports (Redis Streams) re-deliver on restart; the Inbox absorbs duplicates.
2. **Per-sender FIFO** — messages from the same sender are drained in arrival order. Prevents "post deleted" arriving before "post created" when both come from the same producer.
3. **Dead-letter after N failures** — `nack()` increments the counter. At `max_retries`, the message moves to dead-letter storage and is never delivered again.

**`notify=False`** — callers that enqueue their own run (like `Runtime.submit`) pass `notify=False` to suppress the deliver-hook and avoid spawning a duplicate run.

---

## Scheduler — Work Queue and Admission Control

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TB
    classDef src fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E
    classDef sched fill:#E3F2FD,stroke:#1565C0,stroke-width:2px,color:#0D47A1,font-weight:bold
    classDef worker fill:#E8F5E9,stroke:#2E7D32,stroke-width:1.5px,color:#1B5E20,font-weight:bold
    classDef lease fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C
    classDef dead fill:#FFEBEE,stroke:#C62828,stroke-width:1px,color:#B71C1C

    INBOX_EV["Inbox delivery"]:::src
    TIMER_EV["Timer fire"]:::src
    SIGNAL_EV["Signal fire"]:::src
    CHILD_EV["Child completion"]:::src

    SCHED["Scheduler\nenqueue(run_id, priority, tenant, wake)\nlease(worker_id, capacity)\nheartbeat(lease)\nrelease(lease, status, wake_on)\nfind_run_for_agent(agent_id)\nwake_suspended(run_id)\nwake_agent(agent_id)"]:::sched

    LEASE["Lease\nrun_id · agent_id\nworker_id · expires_at\nattempt: int\n\nMust heartbeat every\n(expires_at - now) / 2"]:::lease

    W1["Worker 1"]:::worker
    W2["Worker 2"]:::worker

    DEAD["Dead-run storage\n(FAILED + retries exhausted)"]:::dead
    RETRY["Re-enqueue\n(FAILED + retries remaining)"]:::sched

    INBOX_EV & TIMER_EV & SIGNAL_EV & CHILD_EV -->|"enqueue (coalesced)"| SCHED
    SCHED -->|"lease()"| W1
    SCHED -->|"lease()"| W2
    W1 -->|"heartbeat()"| SCHED
    W1 -->|"release(SUSPENDED)"| SCHED
    W2 -->|"release(COMPLETED)"| SCHED
    W2 -->|"release(FAILED)"| SCHED
    SCHED -->|"retries exhausted"| DEAD
    SCHED -->|"retries remain"| RETRY

    SCHED --> LEASE
```

**Coalescing guarantee** — if a timer fires AND a message arrives while a run is already pending, the Scheduler merges them into one entry. Workers receive a run at most once per wake-cycle regardless of how many sources fired.

---

## Wakeup — What Resumes a Suspended Run

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph LR
    classDef wakeup fill:#E8EAF6,stroke:#3949AB,stroke-width:1.5px,color:#1A237E,font-weight:bold
    classDef kind fill:#E8F5E9,stroke:#2E7D32,stroke-width:1px,color:#1B5E20
    classDef bus fill:#FFF3E0,stroke:#E65100,stroke-width:1px,color:#BF360C

    WU["Wakeup (frozen)\nkind: message|timer|signal|child_done\nat: datetime | None\nsignal: str | None\npayload: dict\nsource_run: RunId | None\nchild_run: RunId | None\nresult_ref: str | None"]:::wakeup

    MSG["message\nsource_run identifies sender"]:::kind
    TMR["timer\nat: the datetime that expired"]:::kind
    SIG["signal\nsignal: name + payload"]:::kind
    CD["child_done\nchild_run + result_ref (ArtifactStore ref)"]:::kind

    SB["SignalBus (Protocol)\nsignal(run_id, name, payload)\ntimer(run_id, at: datetime)\n\nA signal fired before suspend\nis buffered for next wake"]:::bus

    WU --> MSG
    WU --> TMR
    WU --> SIG
    WU --> CD
    SB --> WU
```

`Wakeup` is both the trigger carried by the Scheduler and the payload of the `run.suspended` log entry. Every suspension is replayable — you can always know exactly why a run went dormant.

---

## Supervisor — Spawn, Join, Cancel

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'actorBkg': '#E8EAF6','actorBorder': '#3949AB','actorTextColor': '#1A237E','activationBkgColor': '#E3F2FD','activationBorderColor': '#1565C0','noteBkgColor': '#FFFDE7','noteBorderColor': '#F57F17','signalColor': '#546E7A','signalTextColor': '#263238','fontSize': '13px'}}}%%
sequenceDiagram
    autonumber
    participant Parent as "Parent Run"
    participant Supervisor
    participant ParentLog as "Parent EventLog"
    participant Scheduler
    participant Child as "Child Run"

    Parent->>+Supervisor: spawn(child_agent, boot=msg, supervision=child_sv)

    rect rgb(232, 234, 246)
        note over Supervisor,ParentLog: Replay-deterministic: journal before enqueue
        Supervisor->>ParentLog: append("child.spawned", child_run_id)
    end

    Supervisor->>Scheduler: enqueue(child_run_id)
    Supervisor-->>-Parent: RunHandle(child_run_id)

    Parent->>+Supervisor: join(handle)
    Supervisor->>ParentLog: append("run.suspended", wake=child_done)
    Supervisor->>-Scheduler: release(parent_lease, SUSPENDED)

    note over Child: Child runs on any available worker

    Child-->>Supervisor: _complete(child_run_id, RunResult)
    Supervisor->>ParentLog: append("child.completed")
    Supervisor->>Scheduler: wake_suspended(parent_run_id)

    note over Parent: Parent resumes on next lease
    Parent->>+Supervisor: join returns
    Supervisor-->>-Parent: RunResult(status, output)
```

**Four hard properties:**
1. **Replay-deterministic spawn** — `spawn` appends `child.spawned` to the parent's log BEFORE enqueuing. On replay, the same `child_run_id` is returned — never a duplicate child.
2. **Mobile children** — a child is its own run with its own EventLog. Any worker can pick it up.
3. **Budget, not depth** — `spawn` consults `SpawnBudget` on the root `run_id`. Over budget → `SpawnDenied`. No per-branch depth limits.
4. **Cancellation cascade** — `cancel()` delivers cancel wakeups to direct children, which cascade recursively. Each child honours the cancel at its next `ctx.check()`.

---

## FollowGraph and FanoutStrategy — The Social Layer

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'actorBkg': '#E8EAF6','actorBorder': '#3949AB','actorTextColor': '#1A237E','activationBkgColor': '#E3F2FD','activationBorderColor': '#1565C0','noteBkgColor': '#FFFDE7','noteBorderColor': '#F57F17','signalColor': '#546E7A','signalTextColor': '#263238','fontSize': '13px'}}}%%
sequenceDiagram
    autonumber
    participant InfoAgent as "trades-watcher"
    participant RunCtx as "RunContext (L1)"
    participant Fanout as "FanoutStrategy"
    participant Graph as "FollowGraph"
    participant InboxA as "Inbox (alice)"
    participant InboxB as "Inbox (bob)"

    InfoAgent->>+RunCtx: ctx.emit(TopicId("market.trades", run_id), msg)
    RunCtx->>+Fanout: publish(topic, msg, graph, inbox)

    Fanout->>+Graph: followers_of(topic)
    Graph-->>-Fanout: [alice_id, bob_id]

    Fanout->>InboxA: deliver(alice_id, msg)
    Fanout->>InboxB: deliver(bob_id, msg)

    Fanout-->>-RunCtx: done
    RunCtx-->>-InfoAgent: emitted

    note over InboxA,InboxB: Alice and Bob wake on next\nScheduler poll cycle
```

`FollowGraph` is durable — subscriptions survive restarts. `FanoutStrategy` is swappable — Stage 0 does synchronous push to every follower; Stage 3 uses a push/pull hybrid for viral agents with millions of followers.

---

## AskOutcome — The Four Cases

`RunContext.ask()` sends a message to a target agent and awaits its reply. The result is always one of four distinct outcomes — **never collapse them**:

```mermaid
%%{init: {'theme': 'base', 'themeVariables': {'primaryColor': '#E8EAF6','primaryTextColor': '#1A237E','primaryBorderColor': '#3949AB','lineColor': '#546E7A','background': '#FAFAFA','fontSize': '13px'}}}%%
graph TB
    classDef ok fill:#E8F5E9,stroke:#2E7D32,stroke-width:1.5px,color:#1B5E20,font-weight:bold
    classDef warn fill:#FFF3E0,stroke:#E65100,stroke-width:1.5px,color:#BF360C
    classDef err fill:#FFEBEE,stroke:#C62828,stroke-width:1.5px,color:#B71C1C
    classDef bug fill:#FCE4EC,stroke:#880E4F,stroke-width:2px,color:#880E4F,font-weight:bold

    ASK["ctx.ask(target_agent, msg, timeout)"]:::ok

    REPLIED["replied\nTarget finished within timeout\nresult: RunResult"]:::ok
    TIMEDOUT["timed_out\nCaller's patience expired\nTarget is still RUNNING\nhandle: RunHandle (still alive)"]:::warn
    FAILED["target_failed\nTarget's lease expired (worker died)\nSafe to retry — spawn a new run"]:::err
    CANCELLED["target_cancelled\nTarget was explicitly cancelled\nDo not retry — caller or parent cancelled it"]:::err

    BUG["DANGER: Collapsing timed_out + target_failed\nis the canonical duplicate-agent bug\ntimed_out = still alive, do NOT respawn\ntarget_failed = dead, safe to respawn"]:::bug

    ASK --> REPLIED
    ASK --> TIMEDOUT
    ASK --> FAILED
    ASK --> CANCELLED

    TIMEDOUT -.- BUG
    FAILED -.- BUG
```
