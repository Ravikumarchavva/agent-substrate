# System Architecture Audit — 2026-07-02

!!! warning "Superseded — read as history, not current state"
    This audit is a point-in-time snapshot. The remediation program it
    triggered (Phases 0-5, see `../roadmap.md`) is now mostly **Done**:
    `PostgresSignalBus`, `PostgresSupervisor`, and durable
    `SuspendInterrupt`-based suspend/resume shipped in Phase 1 (2026-07-03);
    horizontally-scalable serving, full tenant_id threading, and initial
    `human_gate` signal convergence shipped in Phases 2-4 (2026-07-03);
    tracing/webhook-HMAC/agent-versioning hardening shipped in Phase 5
    (2026-07-04). The "Executive verdict" and per-area findings below
    describe the state **before** that program — check `../roadmap.md`'s
    "Phase status" table before treating any specific finding here as still
    open.

**Scope:** the whole system, audited as three parallel deep-dives:
(A) runtime execution path (`agents/runtime/`, `infrastructure/runtime/`),
(B) serving + state layer (`serving/monolith/`, `serving/services/`, session/memory),
(C) multi-agent orchestration + messaging (`agents/core/`, supervision, inbox, fanout, triggers).

**Goal audited against:** enterprise-grade event-sourced agent platform —
every action persisted as an event, safe retry/replay, horizontal scale-out,
multi-tenant isolation.

**Relationship to the kernel audit:** [`../kernel/2026-07-02-audit.md`](../kernel/2026-07-02-audit.md)
covered kernel contracts vs. Stage-0 implementations. This audit goes wider
and found the kernel audit's headline gap (suspension never updates the
Scheduler) is one instance of a systemic pattern: **the entire coordination
layer is in-process memory, even in "durable" Postgres mode.**

---

## Executive verdict

- **Single-run durability: real.** Postgres EventLog (append-only, optimistic
  concurrency, LISTEN/NOTIFY tail), Postgres Scheduler (`SELECT FOR UPDATE
  SKIP LOCKED` leasing, heartbeat, reclaim), Postgres Inbox (idempotent
  deliver, ack/nack, dead-lettering), journaled LLM/tool effects wired into
  the production ReAct loop.
- **Coordination layer: in-process memory everywhere.** SignalBus, Supervisor,
  ask/reply, HITL bridges/futures, cancel registry, thread locks, follow
  graph/fanout — none survive a restart, none work across 2+ processes. The
  factory (`infrastructure/runtime/factory.py:60-86`) injects Postgres for
  EventLog/Inbox/Scheduler and Redis for Journal but **never** injects
  SignalBus/Supervisor/FollowGraph/Fanout; `runtime.py:88,204-209` defaults
  them to in-memory unconditionally.
- **Enterprise features: largely aspirational.** Multi-tenancy is a docstring
  (plus one real security hole), budgets/deadlines/preemption are dead code,
  per-tenant fairness unimplemented, agent-loop tracing exists but is never
  attached, cron schedules die on restart, webhooks aren't idempotent.
- **Microservices: correctly-shaped scaffolding.** They share the real
  runtime (good) but `human_gate` has zero callers in `agent_runtime`, and
  feature coverage is ~20% of the monolith.

---

## CRITICAL findings

### C1. SignalBus always in-process → ask/reply/sleep don't cross workers
`factory.py` never passes a `signal_bus`; `runtime.py:88` defaults to
`InMemorySignalBus` (per-process dict, `_signal_bus.py:33`). `ctx.ask()`
waits on the local bus (`context.py:216`); `ctx.reply()` signals the local
bus (`context.py:259`). Asker on worker A + replier on worker B = the reply
sets an `asyncio.Event` in B's memory that A never sees; the ask silently
times out. **Headline blocker for horizontal scaling.**

### C2. Supervisor always in-process → join/record_completion cross-process broken
`InMemorySupervisor` holds `_results`/`_events` in memory
(`_supervisor.py:45-47`); constructed unconditionally in `runtime.py:204-209`
even in Postgres mode. Parent on A joining a child leased by B awaits an
`asyncio.Event` forever. Sub-agent **tree topology is not durable**: on parent
restart, `children_of()` returns nothing, `join()` can never resolve.

### C3. EventLog cross-process advisory lock broken by Python hash randomization
`pg_event_log.py:226-227`: `_lock_key = hash(run_id) & mask`. CPython salts
`str.__hash__` per process, so two workers compute **different** lock keys
for the same run — the "two workers racing on one run always serialize"
guarantee doesn't hold; the loser gets a raw asyncpg `UniqueViolation`
instead of `ConcurrentAppendError`. *(Fixed in Phase 0 of the remediation
program — stable sha256-derived key.)*

### C4. Cross-tenant IDOR — any authenticated user reaches any thread
`chat.py:506` → `get_thread` (`thread_service.py:40-43`) selects by
`Thread.id` only — **no `user_id`/`tenant_id` check anywhere on the path**.
Any valid JWT can POST /chat into any thread_id, stream its history, persist
into it. Same pattern on cancel and thread routes. `AuthClaims` carries
`tenant_id`/`sub` (`shared/auth/claims.py`) but routes only check token
*validity*. **Security finding, not just architecture.** *(Fixed in Phase 0.)*

### C5. Journal TTL breaks at-most-once for long runs
`RedisJournal.record` sets `ex=86400` (`redis_journal.py:32,47`). A run
suspended/orphaned >24h loses its effect journal; on replay every lookup
misses → **LLM re-billed, tools re-executed**. Also: `factory.py:78-79`
silently falls back to `InMemoryJournal` when `redis_url` is omitted —
replay becomes process-local without warning. *(Remediation: Phase 1 PR3
makes the EventLog the source of truth for effect results and deletes the
Redis journal from the correctness path.)*

## HIGH findings

### H1. Suspension pins a worker Task + lease; SUSPENDED status never used
`release()` is only called with COMPLETED/CANCELLED/FAILED
(`worker.py:294,305,350`); the 15s heartbeat (`worker.py:261-271`) renews the
lease for the entire suspension. `wake_suspended`/`wake_agent`
(`pg_scheduler.py:204-229`) match zero rows — **dead code**. Long HITL waits
hold worker concurrency and DB leases indefinitely; `reclaim_orphans
(all_running=True)` at monolith startup requeues suspended runs as if
crashed.

### H2. Crash-window around suspending tools loses HITL state
`ctx.tool()` journals only after the tool returns (`context.py:492-501`);
`ask_human` suspends *inside* the call. Crash while suspended → journal miss
→ replay re-executes the tool → fresh `uuid4()` request_id → orphaned UI
card; crash after answer but before record → answer silently dropped.
*(Remediation: hierarchical effect paths + journaled `ctx.uuid()` request_id
— Phase 1 PR2/PR5.)*

### H3. Flat `_step_seq` = latent replay-divergence bug
`effect_id` uses a flat per-run counter (`context.py:390,470`). If a
journal-hit tool skips execution, its *nested* effects never bump the
counter → every subsequent effect_id diverges → journal misses cascade
(re-billing). Found during remediation design; prerequisite fix for
everything else (Phase 1 PR2: hierarchical effect paths).

### H4. Inbox wakeup + inbox drain are process-local / nondeterministic
Wakeup hook only fires in the delivering process (`pg_inbox.py:111-112` →
`runtime.py:92-102`); no LISTEN/NOTIFY on the inbox. Drain
(`worker.py:247`) is a nondeterministic replay input — a message arriving
during suspension changes the replayed prompt sequence. *(Remediation:
journaled `inbox.drain` — Phase 1 PR5.)*

### H5. Serving layer is single-replica by construction
Per-process state that breaks with 2 replicas (evidence in audit B):
- `thread_locks` (`dependencies.py:35`, used `chat.py:513`) → **duplicate
  concurrent runs** per thread across replicas.
- `cancel_registry` (`cancel.py:35`) → **cross-replica cancel is a silent
  no-op** (`{"status":"not_found"}`).
- `WebHITLBridge._pending` futures + `BridgeRegistry._bridges`
  (`bridge.py:127,472,542`) → approvals can't resolve cross-replica; deny
  after 300s timeout.
- SSE queues bound to the connection-holding replica.

### H6. External cancel doesn't cascade; deadlines never enforced
`worker.cancel` (`worker.py:91-131`) never calls `supervisor.cancel` —
children keep running as orphans. `RunMeta` is built with no deadline and no
supervision (`worker.py:216`); `ExecutionBudget.deadline_s` is dead code.
Child crash reaches the parent only via full `ask_timeout` (120s default) —
no crash→reply fast-path (`orchestrator.py:203` vs `worker.py:308-353`).

### H7. Memory-seed race + partial-eviction truncation
`load_session_memory` hit test is `count_messages > 0` (`factory.py:189`) —
a boolean, not completeness. Redis `LTRIM` cap (`redis_history.py:135-136`)
or partial eviction → treated as hit → **silently truncated context, never
reseeded**. Two replicas seeding the same thread concurrently → duplicated
history (no idempotency guard on seed).

## MEDIUM findings

- **M1. Per-tenant fairness unimplemented**: `lease()` orders by
  `priority, enqueued_at` only (`pg_scheduler.py:291-297`); tenant column
  stored but unused for scheduling. One tenant starves all others.
- **M2. Supervision/SpawnBudget/preemption dead code**: `spawn_child()` has
  zero callers; `ctx.spawn` always uses `Supervision.root()`
  (`context.py:295`); `SpawnTracker` is per-message not per-tree
  (`orchestrator.py:132`); pause/preemption (`budget.py:79-149`) has zero
  callers.
- **M3. Fanout/FollowGraph in-memory only**: no durable backend; topic
  subscriptions lost on restart (`_follow_graph.py:20-22`, factory injects
  neither).
- **M4. Retry paths double-count**: scheduler retry (max 3) and inbox nack
  (max 3) are independent counters over the same failure; `run.failed` is
  logged before the retry decision → retried runs log failed-then-completed;
  no backoff; `Lease.attempt` never incremented (`pg_scheduler.py:349-379`).
- **M5. Cold-resume can't rebuild spawned children**: `save_run_spec` never
  called by `submit`/`spawn` (`runtime.py:122-126`, `_supervisor.py:84-85`)
  → orphaned children requeue but can never re-register.
- **M6. Storage growth unbounded**: terminal `ravi_run_queue` rows and event
  logs never cleaned; no retention/compaction anywhere.
- **M7. Agent-loop observability never attached**:
  `AgentTracingMiddleware`/`ChatTracingMiddleware` exist
  (`observability.py:42-131`) but zero instantiations — only FastAPI edge
  spans are live (`app.py:270`).
- **M8. Cron schedules ephemeral**: `TriggerScheduler` uses
  `MemoryDataStore()` (`scheduler.py:73-75`) despite docstring claiming
  Redis job store — schedules die on restart.
- **M9. Webhooks non-idempotent, weak secret**: every POST mints a fresh
  run (`webhooks.py:127-151`); secret check is plain `==` not HMAC
  (`webhooks.py:117`).
- **M10. No agent versioning**: replay runs current code against old effect
  journals; step-sequence changes silently diverge effect_ids — no
  detection.
- **M11. Rate limiting fail-open + not per-provider/tenant**:
  `rate_limit.py:104-105,125` skips limiting when Redis is down; provider
  rate limits are a single in-process token bucket per middleware instance
  (`rate_limiter.py:11-34`).
- **M12. Microservices `human_gate` unwired**: durable HITL design exists
  (`human_gate/service.py:43-123`) but zero references in `agent_runtime` —
  the agent never blocks on it.

## What genuinely works (don't re-litigate)

- Lease claim via `FOR UPDATE SKIP LOCKED` (`pg_scheduler.py:291-297`).
- Event-log tail via LISTEN/NOTIFY + 2s poll backstop, cross-process
  (`pg_event_log.py:79-101,149,204-209`).
- Inbox idempotent deliver + dead-lettering (`pg_inbox.py:97-181`).
- Journal `SET NX` write-once (`redis_journal.py:47`) — TTL is the flaw, not
  the semantics.
- `ReActAgent` genuinely uses journaled `ctx.llm`/`ctx.tool`
  (`react.py:161,191`).
- Scheduler retry policy is enforced (with M4 caveats).
- Rate limiting is Redis-backed and replica-safe (with M11 caveats).
- Microservices share the runtime rather than forking it
  (`agent_runtime/service.py:9,28,58`).

---

## Remediation

The approved program (see [`../roadmap.md`](../roadmap.md) and the session
plan) addresses these in five phases: **Phase 0** security/correctness
stopgaps (C3, C4, M11-fail-open) → **Phase 1** durable coordination core
(C1, C2, C5, H1-H4, H6: hierarchical effect paths, event-log-as-journal +
fold, PostgresSignalBus, durable suspend/resume via `SuspendInterrupt`,
PostgresSupervisor, cancel cascade + deadlines) → **Phase 2** horizontally
scalable serving (H5, H7) → **Phase 3** full multi-tenancy (C4 depth, M1,
M2 budgets) → **Phase 4** microservices as the scale path (M12, M8 + feature
parity) → **Phase 5** enterprise hardening (M7, M9, M10, M6).

Key architecture decisions locked during planning (recorded in
[`../decisions.md`](../decisions.md) as they land):
- **Postgres for all coordination** (signals/supervision/wakeups/cancel) —
  transactional with EventLog + scheduler; LISTEN/NOTIFY proven pattern.
- **EventLog is the single source of truth for effect results**; the journal
  becomes a per-run in-memory fold (`EffectCache`) built at lease time; Redis
  leaves the correctness path.
- **Suspension = `SuspendInterrupt` (BaseException) + replay-from-top with
  journal fast-forward** — no coroutine pickling, no "effect started" table;
  pre-wait tool bodies re-execute deterministically.
- **Hierarchical effect paths** replace the flat `_step_seq` (H3) before
  anything else builds on effect identity.

## Verification commands for the next audit

```bash
# Coordination backends actually injected? (should show pg_signal_bus/pg_supervisor after Phase 1)
grep -n "signal_bus\|supervisor" src/substrate/infrastructure/runtime/factory.py

# SUSPENDED actually used?
grep -rn "SUSPENDED" src/substrate/agents/runtime/worker.py src/substrate/infrastructure/runtime/pg_scheduler.py

# IDOR: ownership enforced?
grep -n "user_id" src/substrate/serving/monolith/services/thread_service.py

# Advisory lock stable?
grep -n "_lock_key\|sha256" src/substrate/infrastructure/runtime/pg_event_log.py

# Effect results in the event log?
grep -rn "effect.result" src/substrate/agents/runtime/context.py

# Tracing middleware attached?
grep -rn "TracingMiddleware" src/substrate/infrastructure/serving_factory.py
```
