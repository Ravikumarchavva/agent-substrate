# Scalable Agent Runtime — Design Plan

> Realizes the runtime described in [`/GOAL.md`](../../../GOAL.md): a **durable
> message fabric over a follow-graph of mostly-dormant, addressable agents**.
> Kernel-first, no backward compatibility, scalable but simple.

---

## The one principle that makes this work

**Define the kernel contracts once; never change them. Every scaling stage only
swaps the implementation behind a contract.**

- Stage 0 runs entirely **in-process** (in-memory impls) — ships in weeks.
- Stage 1 swaps in **Postgres** behind the same contracts — durability, no rewrite.
- Stage 2/3 swap in **Redis Streams / NATS / a distributed scheduler** — scale,
  no rewrite.

The agent author writes the same code at every stage. The kernel stays tiny
(~8 small contract files). All complexity lives in swappable implementations in
`agents/` (L1) and `integrations/` (orthogonal). This is the entire "simple now,
scales later" bet.

---

## Design goals (from GOAL.md, made testable)

| Goal | Concrete property | Verified by |
|---|---|---|
| **Idle ≈ free** | A dormant agent holds zero coroutines, zero RAM — just rows in storage | 1M registered agents, <100 live; memory flat |
| **Durable** | A run survives process crash and multi-day pause, resumes exactly | Kill worker mid-run; resume produces identical result |
| **Mobile** | Any worker can pick up any run | Run started on worker A finishes on worker B |
| **Accountable** | Full replay of what an agent did; effects at-most-once | Replay log → same state; crash mid-effect → no double email |
| **Social** | Agents follow, message, and wake each other | Producer emits → N follower agents woken with the message |
| **Multi-tenant** | Isolation + per-tenant fairness + backpressure | One tenant's burst can't starve another |
| **Simple** | Kernel is contracts only; ~8 files; in-process impl works standalone | Kernel invariant tests; runs with zero external services |

---

## Core model

### 1. The run is an event-sourced, suspendable unit

A run's **truth is an append-only log of `RunEvent`s.** Current state is a *fold*
over the log. This single decision buys durability, resumability, mobility, live
streaming, replay/VOD, and time-travel debugging — all from one mechanism.

`Checkpoint` (which exists today) is demoted from "source of truth" to a **log
compaction snapshot** — an optimization to bound rehydration cost, not the
authority.

### 2. The agent is a durable coroutine, not a request handler

Today's `Agent.on_message(ctx, payload) -> reply` assumes a synchronous reply in
one in-process call. That cannot survive a crash or a multi-day suspend. We
replace it with a **durable coroutine**: the author writes ordinary imperative
async code, and the *await points are durable*.

```python
async def run(self, ctx: DurableContext, inbox: list[Message]) -> None:
    for msg in inbox:
        facts   = await ctx.llm(extract_prompt(msg))        # journaled
        ranked  = await ctx.tool("rank", facts, user=ctx.owner)  # journaled
        await ctx.store(ranked)                              # journaled effect
    await ctx.sleep_until_signal("new_source_item")          # suspends → 0 cost
```

On crash/resume the function re-runs from the top, but **journaled await points
return their cached result instead of re-executing** — so the LLM call isn't
repeated and the email isn't re-sent. The author writes normal code; the runtime
gives it suspend/resume/replay. (The kernel stays neutral — it only defines the
`Journal`/`Effect`/`EventLog` contracts; the durable-coroutine ergonomics are an
L1 concern via `DurableContext`. A reducer-style agent is still expressible.)

### 3. The agent is a durable, addressable citizen with an inbox

Delivery to a dormant agent is what *wakes* it. The agent lifecycle:

```
dormant (0 RAM, 0 CPU — rows in storage)
   │  message delivered to Inbox  →  Scheduler enqueues a wakeup
   ▼
worker leases the run  →  folds event log to rebuild state
   →  drains inbox (batched)  →  runs the durable coroutine
   →  persists new events + effects  →  emits messages / suspends
   ▼
dormant again  (until next delivery, timer, or signal)
```

### 4. The follow-graph is the social fabric

"Agent A follows agent B" is `A.follow(TopicId(type="agent.posts", source=B.key))`.
When B emits, **fan-out** delivers to every follower's inbox. Built on the
existing `publish_message` / `subscribe` / `TopicId` / `Subscription` primitives —
elevated and made durable, not replaced.

---

## The minimal kernel surface (L0 — contracts only)

New package `kernel/runtime/` (sketches; signatures illustrative):

```python
# ids.py
RunId = str                               # ULID — globally unique, time-sortable
class RunStatus(str, Enum):
    PENDING; RUNNING; SUSPENDED; COMPLETED; FAILED; CANCELLED

# log_entry.py — the durable spine
# NOTE: named RunLogEntry (not RunEvent) to avoid collision with kernel/events.py::Event
class RunLogEntry(BaseModel, frozen):
    run_id: RunId; seq: int; kind: str; payload: JsonObject; ts: datetime
class EventLog(Protocol):
    async def append(self, run_id, entry, *, expected_seq: int) -> int   # optimistic concurrency
    def read(self, run_id, *, from_seq=0) -> AsyncIterator[RunLogEntry]
    def tail(self, run_id, *, from_seq) -> AsyncIterator[RunLogEntry]    # live viewers (UI/SSE only)
    async def last_seq(self, run_id) -> int

# effects.py — idempotent external effects (the at-most-once guarantee)
class Effect(BaseModel, frozen):
    id: str                               # deterministic: hash(run_id, step_seq, kind, args)
    kind: str; spec: JsonObject
class EffectResult(BaseModel, frozen):
    effect_id: str; status: Literal["ok","error"]; value: JsonObject; artifact_ref: str | None = None
class Journal(Protocol):
    async def lookup(self, effect_id) -> EffectResult | None
    async def record(self, result: EffectResult) -> None

# inbox.py — durable mailbox
class Inbox(Protocol):
    async def deliver(self, agent_id: AgentId, msg: Message) -> bool    # True=new, False=dedup
    async def drain(self, agent_id: AgentId, *, max: int) -> list[Message]
    async def ack(self, agent_id: AgentId, msg_id: str) -> None
    async def nack(self, agent_id: AgentId, msg_id: str, *, error: str) -> None

# follow_graph.py — the social follow-graph
# NOTE: named FollowGraph (not SubscriptionGraph) to avoid collision with kernel/graph.py::GraphStore (RAG)
class FollowGraph(Protocol):
    async def follow(self, follower: AgentId, topic: TopicId) -> Subscription
    async def unfollow(self, sub: Subscription) -> None
    async def followers_of(self, topic: TopicId) -> AsyncIterator[AgentId]
    async def following(self, agent: AgentId) -> AsyncIterator[TopicId]

# fanout.py — how an emit reaches followers
class FanoutStrategy(Protocol):
    async def publish(self, topic, msg, *, graph: FollowGraph, inbox: Inbox) -> None

# wakeup.py — what resumes a dormant run
class Wakeup(BaseModel, frozen):          # timer | signal | child_done | message
    kind: Literal["message", "timer", "signal", "child_done"]
    at: datetime | None = None            # timer
    signal: str | None = None             # signal name
    payload: JsonObject = {}              # signal data
    source_run: RunId | None = None       # message source
    child_run: RunId | None = None        # child_done: which child finished
    result_ref: str | None = None         # child_done: ArtifactStore ref to RunResult
class SignalBus(Protocol):
    async def signal(self, run_id: RunId, name: str, payload: JsonObject) -> None
    async def timer(self, run_id: RunId, at: datetime) -> None

# scheduler.py — placement, leasing, admission control
class RunRetryPolicy(BaseModel, frozen):
    max_retries: int = 3; backoff_s: float = 5.0
class Lease(BaseModel, frozen):
    run_id: RunId; worker_id: str; expires_at: datetime; attempt: int = 0
class Scheduler(Protocol):
    async def enqueue(self, run_id, *, priority: int, tenant: str,
                      wake: Wakeup | None = None,
                      retry_policy: RunRetryPolicy | None = None) -> None
    async def lease(self, *, worker_id: str, capacity: int) -> list[Lease]
    async def heartbeat(self, lease: Lease) -> None
    async def release(self, lease: Lease, *, status: RunStatus,
                      wake_on: Wakeup | None = None) -> None
    async def pending_runs(self, *, tenant: str | None) -> AsyncIterator[RunId]

# supervisor.py — agents spawning subagents (supervision-v2 on the durable substrate)
class RunHandle(BaseModel, frozen):
    run_id: RunId; agent_id: AgentId; parent_run: RunId
class RunResult(BaseModel, frozen):
    run_id: RunId; status: RunStatus      # COMPLETED | FAILED | CANCELLED
    output: Payload | None = None; error: str | None = None; metadata: JsonObject = {}
class SpawnDenied(KernelError): ...       # SpawnBudget exhausted — handle like a denied tool
class Supervisor(Protocol):
    async def spawn(self, child: AgentId, *, parent: RunId,
                    supervision: Supervision, boot: Message) -> RunHandle: ...
    async def cancel(self, handle: RunHandle, *, reason: str) -> None: ...  # cascades subtree
    async def children_of(self, parent: RunId) -> AsyncIterator[RunHandle]: ...  # crash reconciliation

# agent.py — the revised agent contract
class DurableAgent(Protocol):
    id: AgentId
    async def run(self, ctx: DurableContextProtocol, inbox: list[Message]) -> None
```

---

## Unified communication model: `ask` / `send` / `reply`

### The core insight: one mechanic, two relationships

Whether you're talking to a child you spawned or a peer agent, *awaiting a reply*
is always the same substrate:

1. Deliver a message with a correlation key
2. Suspend (0 RAM) — `Wakeup(kind="signal", signal=f"reply:{correlation_id}")`
3. Wake when a correlated reply arrives, OR a timeout fires, OR the target dies

**`spawn` is lifecycle only.** It records ownership (`child_spawned` in the parent
log), mints a `RunHandle`, and enqueues the child. It does NOT wait. The wait is
always a separate `ask`. This means `ask` works identically for children and peers —
the only difference is what you can do *after* a timeout.

### `AskOutcome` — timeout ≠ failure

```python
class AskOutcome(BaseModel, frozen):
    kind: Literal["replied", "timed_out", "target_failed", "target_cancelled"]
    result: RunResult | None        # set when kind="replied"
    handle: RunHandle | None        # B's STILL-LIVE run on timed_out (not failed)
    last_seq: int                   # how far B's EventLog got — its progress
```

A bare timeout tells you nothing about the target. These are the four distinct outcomes:

| `kind` | What it means | Right action |
|---|---|---|
| `replied` | B finished in time | take the result |
| `timed_out` | A's patience expired; B **alive** (lease heartbeating) | extend wait, or for owned children: `cancel_and_resume` |
| `target_failed` | B's lease **expired** (worker died) | safe to retry; Journal deduplication prevents re-doing completed effects |
| `target_cancelled` | B was explicitly cancelled | treat as an error or escalate |

Never collapse `timed_out` and `target_failed` — that's the bug that spawns a duplicate
agent while the original is still running.

### `DurableContext` communication surface (L1, not kernel)

```python
# fire-and-forget — A does not suspend
async def send(self, target: AgentId, msg: Message) -> None

# await reply — A suspends until outcome; mandatory timeout prevents infinite waits
async def ask(
    self,
    target: AgentId | RunHandle,
    msg: Message,
    *,
    timeout: float,
    idempotency_key: str | None = None,  # for peer re-ask without duplication
) -> AskOutcome

# reply to an ask — signals msg.reply_to with the result
async def reply(self, to: Message, result: Payload) -> None

# opt-in batched peek — NOT streaming; returns current status + last milestone
async def status(self, handle: RunHandle) -> RunStatusSummary
```

`join(handle)` from the original design collapses into `ask(handle, boot_msg,
timeout=...)` — it is not a separate primitive. Keeping it as a named alias is fine
for readability, but it is built on the same correlation+timeout+watch substrate.

### `Message` carries the reply address (kernel/message.py extension)

```python
class Message(BaseModel):
    ...                              # existing fields unchanged
    reply_to: RunId | None = None    # where the reply signal goes (the asker's run_id)
    correlation_id: str | None = None  # matches reply to request (default: Message.id)
```

Both fields default to `None` for fire-and-forget messages. Set by `DurableContext.ask`
automatically — the agent author never touches them.

### Who decides ask vs send? The action definition, not the LLM.

The LLM never chooses `ask` vs `send`. It picks an **action**; the action's declared
shape decides the verb — baked in at authoring time, not at call time:

| Action exposed to LLM | Underlying verb | Deciding factor |
|---|---|---|
| Tool call / delegate-to-agent | `ask` | the loop cannot continue without the result |
| Publish to followers / notify | `send` | declared fire-and-forget |
| Reply to an incoming message | `reply` | responding to a `message.reply_to` |

A failed `ask` (timeout, target_failed) surfaces as a `ToolResultBlock(is_error=True, ...)`
so the model can reason: *"the researcher timed out — let me try the cached source."*

### Retry without zombies — the ordering rule

**For owned children** (you spawned them):
```
timeout/failure → cancel_and_resume(handle)
                → cancel cascades old subtree (no orphan)
                → new run folds from the child's EventLog
                → Journal skips already-completed effects (no double email)
```
There is no API path that spawns a fresh child while the old one is still running.

**For peers** (you do not own them):
```
timeout → re-ask with same idempotency_key
        → Inbox deduplicates the delivery
        → Journal deduplicates any in-progress effects
        → peer recognizes the in-flight task; can't be cancelled but can't be duplicated
```

---

## Progress observation — two consumers, two channels

A common mistake: routing B's live progress through A's reasoning loop. That costs
A's tokens, adds noise, and is almost never needed. The two consumers are distinct:

| Consumer | What they need | Channel | Cost to A |
|---|---|---|---|
| **Human / UI** | every step, live | `AgentProgress` → SSE → browser | zero — never touches A |
| **Parent agent A** | almost nothing | wake-on-`child_done` / `timed_out` | zero — A is dormant |

**A's default is total sleep.** It wakes only at decision points:
- **By rule** (supervision policy): "if child passes 70% of deadline, wake A with a
  batched `AskOutcome`" — the timeout mechanism is the batching mechanism.
- **By LLM (opt-in)**: `ctx.status(handle)` as a callable tool. Returns a compact
  `RunStatusSummary` (status + last milestone + `last_seq`) — not the raw log.
  The model calls it *if it has a reason* ("this is taking long, is it stuck?").

`EventLog.tail()` exists for the **UI/SSE path** — the Gateway serves it directly to
live viewers. It is **not** a parent-agent primitive. Streaming the log into A's
context is an antipattern: it burns tokens and degrades reasoning.

### Subagent spawning — the four hard properties

This realizes the approved **supervision-v2** plan (resumable agent tree + `SpawnBudget`
+ `ExecutionBudget`, run-scoped resumability) on the event-sourced substrate. The
existing `kernel/supervision.py::Supervision` is the **policy** half (tree position,
budget, retention) and is reused as-is — `Supervisor` is the **runtime** half that
actually creates and joins running entities.

1. **Replay-deterministic spawn.** `spawn` appends a `child_spawned{child_run}` entry to
   the *parent's* log before enqueuing the child. On parent replay the journaled spawn
   returns the *same* `child_run` — never a duplicate child. (Same Journal mechanism as
   a tool call.) The child's own log opens with its `boot` message.
2. **Mobile children.** A child is its own run with its own event log → any worker runs
   it. `join` is a suspend point; the `child_done` wakeup carries the child's
   `result_ref`, journaled on the parent. (Temporal child-workflows / Azure Durable
   sub-orchestrations — well-precedented.)
3. **Budget, not depth.** `spawn` consults `SpawnBudget` bound to the **root** run_id;
   over budget → `SpawnDenied` (the author handles it like a denied tool). Child policy
   is minted via the existing `Supervision.spawn_child()`. Per supervision-v2,
   `max_agents`/`depth` ceilings are dropped — the budget is the single constraint.
4. **Cancellation cascade.** `cancel(parent)` appends a cancel intent and the scheduler
   delivers a cancel wakeup to each child run, recursively — the durable analog of the
   in-process `CancellationToken.child()` that exists today.

### Orphan handling on permanent parent failure

A parent can fail two ways, and they are handled differently:

- **Transient crash (worker died).** The scheduler re-leases the parent; it folds its
  log, sees `child_spawned` entries without matching `child_completed`, and re-`join`s
  via `children_of`. The children kept running independently (mobility) — nothing is
  lost, the parent just reattaches.

- **Permanent failure (parent run reaches `FAILED`/`CANCELLED` terminally).** Default
  policy is **cascade-cancel, keyed on each child's `HistoryRetention`:**

  | Child `HistoryRetention` | On permanent parent failure |
  |---|---|
  | `RUN` (default subagent) | **Cancelled** — a run-scoped worker has no meaning without its parent; `Supervisor.cancel` is fired on the whole subtree. |
  | `PERMANENT` (durable citizen) | **Detached, not cancelled** — re-parented to the root (`Supervision.root_id`); it remains a first-class addressable agent in the mesh and goes dormant until its next delivery. |
  | `NONE` (stateless) | Cancelled and its log compacted away — nothing to keep. |

  The cascade is itself logged: the parent's terminal entry records `orphans_resolved`
  with the per-child disposition, so the decision is replayable and auditable. A child
  already mid-effect honors the cancel at its next `ctx.check()`; the at-most-once
  effect guarantee still holds — a cancelled child never double-fires an effect.

---

## Topology (three independently-scalable tiers)

```
            ┌─────────────┐   appends inbound event, enqueues wakeup
  client ──▶│  Gateway    │──────────────────────────────────────────┐
            │ (stateless) │   live viewers tail the EventLog          │
            └─────────────┘◀───────────────────────────────────────┐ │
                                                                    │ ▼
            ┌─────────────┐   leasing · admission · per-tenant   ┌────────────┐
            │  Scheduler  │◀──fairness · backpressure · placement │  EventLog  │
            │(coordination)│                                      │  Inbox     │
            └─────────────┘                                       │  Graph     │
                  │ leases runs                                   │ (durable   │
                  ▼                                               │  stores)   │
            ┌─────────────┐  lease → fold log → drain inbox →     └────────────┘
            │   Workers   │  run coroutine → persist → release          ▲
            │ (stateless, │─────────────────────────────────────────────┘
            │  autoscale) │  effects journaled; runs are MOBILE
            └─────────────┘
```

- **Gateway** — stateless, edge-deployable. Appends inbound events, notifies the
  scheduler. Live "watch this agent" = `EventLog.tail()`. Replay/VOD = `read(0)`.
- **Scheduler** — coordination only, never runs agent logic. Admission control,
  per-tenant fairness, lease management, backpressure. The valve that stops a
  viral agent from melting the cluster.
- **Workers** — stateless, autoscale on lease-queue depth. Because state lives in
  the log, a dying worker just drops its leases and others pick the runs up
  mid-flight.

---

## Implementation phasing (maps to GOAL.md phases)

### Stage 0 — Contracts + in-process runtime · Phase 1 (adoption, ~weeks)
- Write all `kernel/runtime/` contracts.
- Ship simplest impls: **in-memory** EventLog, Inbox, Journal, SubscriptionGraph;
  **single-process** Scheduler (one worker, asyncio); local FanoutStrategy (push).
- Build `DurableContext` + one `DurableAgent` rewrite of `ReActAgent`.
- **Deliverable:** the personal-learning-feed use case runs for one user on one
  node, no external services. Durable *within* the process.
- Replaces today's `LocalRuntime`, reorganized around the new contracts.

### Stage 1 — Durable persistence · Phase 2 (monetize, ~quarter)
- Swap in **Postgres** EventLog (append-only table, `(run_id, seq)` PK, optimistic
  concurrency), Postgres Inbox + SubscriptionGraph. Journal backed by the log.
- Worker pool leases via `SELECT … FOR UPDATE SKIP LOCKED`.
- **Deliverable:** agents survive restarts, multi-day HITL pauses, resume exactly.
  This is the reliability customers pay to depend on. Kernel unchanged.

### Stage 2 — Dormancy + horizontal scale · Phase 3 (scale)
- Dormant-by-default: agents not held in memory; woken by inbox delivery.
- Workers autoscale on queue depth; runs fully mobile (any worker, any run).
- **Log compaction** (snapshots) to bound rehydration cost on long runs.
- Hot path moves to **Redis Streams / NATS JetStream** for throughput; Postgres
  stays the durable system of record. `EventLog.tail()` powers live streaming.
- **Deliverable:** millions of dormant agents at near-zero idle cost. Kernel unchanged.

### Stage 3 — The mesh · Phase 4 (network)
- `FanoutStrategy`: push for normal agents, **pull for celebrity agents** (the
  fan-out-on-read/write hybrid); one durable write on emit, async materialization.
- **Wake-amplification controls** in the Scheduler: batching, coalescing, tiered
  admission so a viral agent doesn't trigger 1M synchronous activations.
- Distributed scheduler (consistent hashing / work-stealing across regions);
  cross-tenant agent identity + trust.
- **Deliverable:** open many-to-many agent network. Kernel unchanged.

> Note the through-line: **only Stage 0 touches the kernel.** Stages 1–3 are pure
> implementation swaps. That is the plan's central guarantee.

---

## What changes in the current kernel

| Action | Items |
|---|---|
| **Keep as-is** | `content.py` (ContentBlock), `identity.py` (AgentId/TopicId), `Subscription`, `Payload`, tool taxonomy, `ArtifactStore`, `stream.py` |
| **Extend** | `RunContext` → `run_id: str` now first-class (was buried in `Supervision.run_id`). `Message` → add `reply_to: RunId \| None` and `correlation_id: str \| None` (both default `None`; set automatically by `DurableContext.ask`). |
| **Demote** | `Checkpoint` → log-compaction snapshot (optimization), not source of truth |
| **Replace** | `protocol.py::AgentRuntime` god-object → split into `EventLog` + `Inbox` + `FollowGraph` + `Scheduler` + `Supervisor` + `SignalBus`. `RuntimeRef`/`AgentRuntime` removed — agents communicate via `ask`/`send`/`reply` on `DurableContext`, not by holding a runtime reference. |
| **Revise** | `agent.py::Agent.on_message` → `DurableAgent.run(ctx, inbox)`. `save_state`/`load_state` removed — state *is* the folded log. `Supervisor.join` collapses into `ask` (same substrate: correlation + timeout + watch); kept as a named alias for readability. |
| **Reuse** | `supervision.py::Supervision` (tree position, `SpawnBudget`, `HistoryRetention`) is the **policy** half; `Supervisor` is the **runtime** half. `max_agents`/`depth` ceilings dropped per supervision-v2. |
| **Add** | `kernel/runtime/`: `ids`, `log_entry`, `effects`, `inbox`, `follow_graph`, `fanout`, `wakeup`, `scheduler`, `supervisor`, `agent`. `AskOutcome`, `RunStatusSummary` value types (for ask/status return). |

Layering (import-linter stays green): contracts in **kernel (L0)**; `DurableContext`,
in-memory impls, `ReActAgent` rewrite in **agents (L1)**; Postgres/Redis/NATS impls
in **integrations** (orthogonal); gateway/scheduler/worker shells in **serving**.

---

## Hard parts, stated honestly

1. **The determinism tax.** Durable replay requires non-journaled agent code to be
   deterministic — no bare `datetime.now()`, `random()`, or un-journaled I/O in the
   coroutine body; all of it goes through `ctx`. This is the single biggest author
   gotcha. Mitigation: `DurableContext` provides `ctx.now()`, `ctx.random()`,
   `ctx.uuid()`; a lint rule flags raw usage inside agent `run()` bodies.
2. **EventLog becomes the most critical dependency.** Its write throughput and
   ordering bound the system. Postgres (logical decoding for tailing) carries
   Stages 0–2; past that, Kafka/NATS-JetStream. Choosing/operating it is the real
   Stage 2 work the kernel deliberately defers.
3. **Wake amplification** (Stage 3) is the genuinely hard scaling problem — 1M
   followers of a celebrity agent must not mean 1M synchronous wakes. Solved by
   batching + coalescing + tiered admission, not by raw fan-out.
4. **Latency on sub-second turns.** Persisting events on the hot path costs vs pure
   in-memory. Mitigation: the `EventLog` contract allows a fast in-memory tier
   *per-run* for ephemeral chat that doesn't need durability — chosen at run start.
5. **Debugging shifts** from stack traces to log inspection — better in aggregate
   (full replay), unfamiliar at first.

---

## Verification & milestones

```bash
cd ravi-engine
uv run lint-imports          # kernel/runtime/ must stay pure L0; contracts KEPT
uv run pytest                # contract conformance: in-memory + Postgres impls pass the SAME suite
```

- **Stage 0 done when:** personal-learning-feed runs for one user, no external
  services, and a contract-conformance test suite passes against the in-memory impls.
- **Stage 1 done when:** the *same* suite passes against Postgres impls unchanged,
  and an agent survives `kill -9` mid-run and resumes to an identical state.
- **Stage 2 done when:** 1M registered agents with <100 live; memory flat; a run
  started on one worker completes on another.
- **Stage 3 done when:** a producer agent with 100k followers emits and all are
  woken within SLA without a synchronous fan-out spike.

The conformance-suite-against-swappable-impls is the linchpin: it's how we prove
"swap the implementation, never the contract" actually holds.
