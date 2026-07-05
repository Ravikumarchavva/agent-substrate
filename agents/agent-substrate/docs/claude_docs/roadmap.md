# Roadmap — The Approved Remediation Program

Last rewritten: 2026-07-02, after the full system architecture audit
([`audits/2026-07-02-system-audit.md`](audits/2026-07-02-system-audit.md))
and user approval of a 5-phase remediation program. This document tracks the
program's phases and status. The full plan (with PR-level detail for Phase 1)
lives in the session plan file; this is the durable summary.

**Program goal (user's words):** a true event-sourced framework — every
action persisted as an event so any run can be inspected, replayed, and
retried safely — at enterprise scale.

**Locked decisions:**
1. Full horizontal scale-out (N stateless workers + N API replicas).
2. Microservices are THE scale path; monolith remains for dev.
3. Full tenant isolation.
4. Postgres for all coordination (signals/supervision/wakeups/cancel —
   transactional with EventLog + scheduler). EventLog becomes the single
   source of truth for effect results; Redis journal leaves the correctness
   path. Suspension = `SuspendInterrupt` unwind + replay-from-top with
   journal fast-forward. Hierarchical effect paths replace flat `_step_seq`.
   No backward compatibility.

## Phase status

| Phase | Content | Status |
|---|---|---|
| **0** | Audit record; IDOR fix (thread ownership); stable advisory-lock key; rate-limiter fail-closed | **Done** (2026-07-02) — `get_owned_thread` + ownership on chat/cancel/hitl-status/threads/tasks/mcp-context; ownership stamped at creation; list scoped per user; legacy NULL-owner threads claim-on-first-access; sha256 `_lock_key`; `RATE_LIMIT_FAIL_OPEN=False` default (503 when Redis down); tests in `tests/serving/test_thread_ownership.py` |
| **1** | Durable coordination core: hierarchical effect paths (**PR2 done**) → event-log-as-journal + fold (**PR3 done**) → PostgresSignalBus + scheduler columns (**PR4 done**) → durable suspend/resume via `SuspendInterrupt` (**PR5 done**) → PostgresSupervisor (**PR6 done**) → cancel cascade + deadlines + crash fast-path (**PR7 done**) → cleanup/GC/docs (**PR8 done**) | **Done** (2026-07-03) — all 7 PRs shipped; Phase 2 (horizontally scalable serving) is next |
| **2** | Horizontally scalable serving: scheduler-enforced single-flight (kill `thread_locks`); cancel via durable signal (kill `cancel_registry`); HITL cross-replica via PostgresSignalBus; SSE-from-any-replica verification; memory-seed idempotency | **Done** (2026-07-03) — see "Recently shipped". Future-based tool-approval → signal migration (kills `WebHITLBridge._pending`) explicitly deferred (see below) |
| **3** | Full multi-tenancy: `tenant_id` through thread ownership + `RunMeta`; per-tenant fair scheduling in `lease()`; wire `Supervision.execution_budget`/`spawn_child()` (was dead code) | **Done** (2026-07-03) — see "Recently shipped". `RedisHistoryProvider`/`GlobalTaskStore` tenant-keying and tenant-level quota aggregation/rate limits explicitly deferred (see below) |
| **4** | Microservices as the scale path: converge `human_gate` onto the Phase-1 signal bus | **Partially done** (2026-07-03) — signal convergence shipped; wiring `agent_runtime` to actually run an HITL-capable tool against it is deferred (see below). Feature-parity porting (files/RAG → triggers/scheduled → pipelines → MCP apps) and k8s replica policy tuning not started — long tail, out of this program's architecture-remediation scope |
| **5** | Enterprise hardening: tracing spans on agent runs/LLM calls/tool calls; webhook idempotency keys + HMAC; agent versioning guard on replay; event-log retention/compaction | **Done** (2026-07-04) — see "Recently shipped". `ChatTracingMiddleware` deleted rather than installed (see below); most of the pre-existing guardrail/infra middleware family found unwireable in its current form (see below) |

Verification gates per phase are specified in the plan (crash/replay harness,
two-worker integration, lost-wakeup race, cancel/deadline cascade, tenancy
isolation, global log invariants). Standard per-merge gates: `uv run ruff
check .` · `uv run pytest` · `uv run lint-imports`.

## Carried-forward items not in the program (opportunistic)

- ~~`agents/core/react.py` `_react()` refactor~~ — **Done** (2026-07-05):
  `_react_loop` split into `_generate_turn` (LLM call + hooks + budget) and
  `_execute_tool_calls` (per-call invoke + record building).
- ~~Connectors framework~~ — **Done** (2026-07-05), taken further than
  planned: rather than building a full connectors framework, the entire
  framework-maintained Spotify integration was deleted
  (`integrations/spotify/` — both `SpotifyAuthService` and `SpotifyService`
  had zero importers; the `spotify_oauth.py` route referenced here was
  already gone). The `spotify-player` skill's own `SKILL.md` already assumed
  Spotify is delivered as external `mcp_spotify_*` MCP tools with tokens
  from "the backend credential store" — never the framework's Python client.
  The generic `serving/monolith/routes/connector_tokens.py` (project-scoped
  Redis cache, `ravi` owns OAuth) already exists for this pattern if a
  framework-side token cache is needed by a future connector.
- ~~Docs debt~~ — **Done** (2026-07-05): root `CLAUDE.md` tech-debt table's
  stale TaskStore-in-memory line removed (`PgTaskStore` has been live under
  Postgres since before this check). HITL section / mkdocs pre-signal
  language not yet re-audited.
- **Test coverage gaps** outside the program's new suites: guardrails,
  middleware, MCP adapter, `fabric/evals`.

## Explicitly deferred from Phase 2-4 (scoped out, not forgotten)

Each of these was evaluated during Phase 2-4 implementation (2026-07-03) and
deliberately scoped out — either because it's a substantially larger feature
than the surrounding architecture fix, or because the real risk it addresses
turned out to be negligible given how the code actually works. Recorded here
so the decision is visible, not silently dropped.

- **Future-based tool-approval → signal migration** (kills
  `WebHITLBridge._pending`/`ToolApprovalHandler`'s Futures). The signal-based
  `ask_human` path was already migrated (pre-program); tool-approval is a
  separate, comparably-sized migration of its own (new suspend-based
  approval primitive, `human_gate` wiring, its own test suite) — not
  attempted alongside the single-flight/cancel/HITL-resolution fixes that
  made up the rest of Phase 2.
- **`RedisHistoryProvider._key`/`GlobalTaskStore` tenant-namespacing.**
  Evaluated and scoped out: both are keyed by `session_id`/`conversation_id`,
  which are UUIDs in every real call path — two different tenants can never
  collide on the same key by construction, so the actual cross-tenant risk
  is negligible. Formally threading `tenant_id` through would require a
  kernel `HistoryProvider` Protocol change rippling through all 3
  implementations (InMemory/Redis/Postgres) for that marginal gain. Revisit
  only if a call path is ever found constructing a `session_id` from
  non-UUID, potentially-colliding input.
- **Tenant-level token/cost aggregation + tenant-scoped rate limits.**
  `RunMeta.tenant_id` is now genuinely populated end-to-end (Phase 3), which
  is the prerequisite this needs — but the aggregation store/read path
  itself (summing `effect.result` usage payloads per tenant, wiring that
  into `serving/shared/rate_limit.py`) is new feature surface, not a fix to
  something already dead/broken.
- **Wiring `agent_runtime` to actually run an HITL-capable tool against the
  now-signal-capable `human_gate`.** `human_gate.resolve_request()` can fire
  the durable `hitl:{request_id}` signal (shipped), and `POST /hitl/request`
  now exists to create the record — but nothing in `agent_runtime` yet
  constructs an `AskHumanTool` with a `suspends_via_signal=True` handler, and
  `app.state.tools` is currently a single static list built once at lifespan
  startup (no per-run tool customization exists yet in that service). Also
  unresolved: `human_gate`'s response body shape (`approved`/`value`) and
  `AskHumanTool._shape_result()`'s expected payload shape (`action`/`value`)
  were built independently and don't fully align — `resolve_request()`'s
  signal payload does a best-effort mapping (see its docstring) that hasn't
  been validated against a real `AskHumanTool` call. This is a genuine new
  feature (new handler class + per-run tool wiring + payload-shape
  reconciliation + its own test suite), not a coordination-layer fix.
- **Feature-parity porting** (files/RAG → triggers/scheduled, incl. moving
  the APScheduler job store off `MemoryDataStore` → pipelines → MCP apps)
  and **k8s replica-policy tuning**. Both are explicitly called out in the
  original plan as a long tail — genuinely multi-week feature/ops work, not
  architecture remediation. Per-service k8s manifests already exist
  (`deployment/k8s/base/runtime/*.yaml`) and are not a monolith-only
  deployment; nothing here blocks scaling replicas today.
- **Event-log snapshot/compaction (`Checkpoint`).** Evaluated during Phase 5
  (2026-07-04): `sweep_terminal_runs` (`infrastructure/runtime/retention.py`,
  shipped in PR8) already handles retention — deleting EventLog/signals/
  tree/spec/queue rows for old *terminal* runs. Compaction (folding a
  long-lived run's effect history into a snapshot so replay doesn't refold
  from entry 0) is the genuinely unbuilt half, but the plan's own guidance is
  explicit: build it "only if measured fold cost exceeds budget at P99" — no
  such measurement exists yet. Building a snapshot format/versioning scheme
  speculatively is exactly the kind of premature abstraction the project's
  coding standards warn against. Revisit if fold latency is ever profiled
  and found to matter.

## Recently shipped (prune over time)

- **Middleware rebuilt as one Protocol/context/pipeline — no different kinds**
  (2026-07-04, final iteration — supersedes both an earlier RunContext-only
  hook and a subsequent three-pipeline `MiddlewareBundle`, per explicit user
  direction: "a middleware is a middleware across the framework, no different
  kinds"). `kernel/agent/middleware.py` now defines exactly one `Middleware`
  Protocol (non-generic), one `MiddlewareStage` enum (`TURN`/`CHAT`/`TOOL`),
  and one minimal `MiddlewareContextProtocol` — deleting the prior
  `AgentRunContextProtocol`/`ChatContextProtocol`/`FunctionContextProtocol`
  trio and the `AgentMiddleware`/`ChatMiddleware`/`FunctionMiddleware` type
  aliases. `agents/middleware/_contracts.py` defines one concrete
  `MiddlewareContext` dataclass (deleting `AgentCallContext`/`ChatContext`/
  `FunctionContext`) with a `stage` field and three precisely-typed result
  slots (`turn_result: AgentRunResult`, `chat_result: LLMResponse`,
  `tool_result: InvocationResult` — kept separate rather than one `Any`,
  since the three result shapes are genuinely different classes). A
  middleware that only cares about one stage declares
  `stages: ClassVar[frozenset[MiddlewareStage]]`;
  `MiddlewarePipeline.execute()` (`agents/middleware/pipeline.py`, its
  duplicate `MiddlewareProtocol` also deleted in favor of importing the
  kernel's `Middleware`) filters to only the middlewares that declared the
  current context's stage before building the call chain — a middleware
  that didn't declare a stage never gets `process()` called for it there.
  `ReActAgent.__init__` (`agents/core/react.py`) takes exactly one
  `middleware: MiddlewarePipeline`; `_handle_message()` builds a
  `MiddlewareContext(stage=TURN, ...)` and sets `c.turn_result` from
  `_react_loop()`'s real `AgentRunResult`. `agents/runtime/context.py`'s
  `RunContext.llm()`/`.tool()` build `CHAT`/`TOOL`-stage contexts around
  their genuine-execution branch only (never the replay-cache-hit branch)
  and set `c.chat_result`/`c.tool_result`; `CacheMiddleware`'s
  skip-`call_next`-on-hit short-circuit and `HistoryTruncatorMiddleware`'s
  in-place `context.messages` mutation both still work exactly as before —
  only the field names and dispatch object changed. All 17 middleware
  classes across 15 files migrated to the new shape (each gained a
  `stages` attribute and renamed `.result` → the matching typed field).
  `agents/factory.py`'s `create_assistant_agent()`/`rebuild_agent()` collapsed
  `agent_guardrails`/`chat_guardrails`/`function_guardrails` into one
  `middleware: list[Middleware] | None` param, appended after the three
  default tracing middlewares in one shared pipeline. `worker.py` stays
  untouched (it already didn't reference middleware). Tests:
  `tests/agents/test_middleware_wiring.py` rewritten — every guardrail now
  wires identically (`agent.middleware = MiddlewarePipeline([...])`)
  regardless of which stage it targets, plus a new test proving one pipeline
  holding TURN/CHAT/TOOL middleware together dispatches all three correctly
  in a single run; `test_middleware.py`/`test_guardrails.py` updated for the
  new context shape, all still passing. Full gate: ruff, lint-imports (5/5
  contracts kept), kernel invariants, and the full suite (443 passed, only
  the same pre-existing environmental failures) all green; zero remaining
  references to any deleted name anywhere in `src/`/`tests/`.
  - **Webhook idempotency + HMAC**: `WebhookRegistry.handle()`
    (`capabilities/triggers/webhooks.py`) now requires an HMAC-SHA256
    signature over the raw request body (`X-Webhook-Signature`, GitHub/
    Stripe-style `sha256=<hexdigest>`) verified with `hmac.compare_digest`,
    replacing the old plain `==` check against a secret sent directly in a
    header (`X-Webhook-Secret`) — which also used to silently skip
    verification if the header was omitted. Added `X-Webhook-Idempotency-Key`
    dedup: retried deliveries with the same key return the original
    dispatch result instead of re-dispatching, via a bounded (1000-entry)
    in-memory LRU cache. Route updated in
    `serving/monolith/routes/triggers.py` to read the raw body once and pass
    it + the new headers through. Tests: `test_webhook_trigger_dispatch`
    updated for the new contract, plus
    `test_webhook_rejects_invalid_signature` and
    `test_webhook_idempotency_key_dedupes_retried_delivery`
    (`tests/capabilities/test_triggers.py`).
  - **Agent versioning guard**: persisted run specs
    (`serving/monolith/routes/chat.py`'s `_agent_spec`) now carry
    `agent_version: substrate.__version__`. `resume_pending_runs()`
    (`infrastructure/serving_factory.py`) checks this against the running
    version before rebuilding an agent from a cold-resumed spec; on
    mismatch, it refuses to replay (never calls `rebuild_agent`/
    `runtime.register`), instead appending a `run.failed` EventLog entry
    (`status: "version_mismatch"`) and terminally failing the run via the
    new `PostgresScheduler.fail_pending_run()` (a direct pending→failed
    transition for specs that were never leased in this process, so no
    `Lease` exists to hand to `release()`) + `Supervisor.finish_run()`. This
    was chosen over silently resuming (which risks the replayed effect path
    diverging from what actually happened once code has moved on) or
    resuming-with-a-warning (which risks the same silent divergence, just
    logged). Test:
    `test_pg_cold_resume_refuses_version_mismatch`
    (`tests/agents/test_runtime_postgres.py`) — built against bare backend
    objects rather than a full `build_postgres_runtime()`, matching
    `test_pg_cold_resume`'s existing pattern, because a live Worker
    (this process's or another sharing the same Postgres DB) leases *any*
    'pending' row agent-agnostically and would otherwise race the test's
    direct insert before `resume_pending_runs` reads it.
  - **Retention/compaction**: evaluated, not re-shipped — `sweep_terminal_runs`
    already covers retention (PR8); compaction/snapshot deliberately not
    built (see "Explicitly deferred").
  - Gate: `uv run ruff check .` clean on all changed files, `uv run
    lint-imports` (5/5 contracts kept), `uv run pytest
    tests/architecture/test_kernel_invariants.py` (10/10 passed), full
    suite 435-439 passed with only pre-existing environmental failures
    (shared rate-limiter state + Postgres lease contention against a live
    `uv run start` process on the same DB — unrelated to this phase's files).

- **Phase 2-4 — horizontally scalable serving, multi-tenancy, and initial
  microservices convergence** (2026-07-03):
  - **Phase 2**: Durable single-flight — `ravi_run_queue` gained a
    `thread_id` column + unique partial index (`WHERE status IN ('pending',
    'running', 'suspended')`); `Scheduler.enqueue(..., thread_id=...)` raises
    the new `ThreadBusyError` (kernel) on conflict; `routes/chat.py` does a
    cheap `find_run_for_thread()` pre-check for a clean 409 in the common
    case, `Runtime.submit()` is the authoritative enforcement (the rare race
    surfaces as a `run.failed` SSE event instead, since a 409 isn't possible
    once SSE headers are sent). `thread_locks`/`cancel_registry` deleted
    entirely from `ServerDependencies`/`app.py`. Cancel is now
    `routes/cancel.py` resolving the active run via `find_run_for_thread()`
    then calling both `Runtime.cancel()` (fast, same-process best-effort)
    and `Supervisor.cancel()` (durable, cross-replica — the actual
    guarantee). `AgentStreamSession` dropped its `cancel_event` entirely;
    cancellation from *any* replica is observed the same way completion
    always was — a `run.cancelled` EventLog entry appearing under tail().
    HITL: new `Scheduler.find_run_by_wake_signal(name)` lets
    `BridgeRegistry.resolve()` fall back to a durable lookup
    (`ravi_run_queue.wake_signals`) when no local bridge owns a
    `request_id` — the cross-replica case. Memory-seed race:
    `RedisHistoryProvider.try_acquire_seed_lock()` (atomic `SET NX EX`)
    guards `agents/factory.py::load_session_memory()`'s seed — closes the
    double-seed-truncates-older-messages bug at the root (prevents the race
    that caused it, rather than working around the truncation symptom).
  - **Phase 3**: `Thread` gained a `tenant_id` column (additive migration in
    `serving/monolith/database.py`, mirroring the `ravi_run_queue` pattern);
    `get_owned_thread()` now requires matching tenant, not just matching
    user, with the same claim-on-first-access affordance for legacy NULL
    rows. `Lease` (kernel) gained a `tenant` field, threaded from
    `PostgresScheduler`/`InMemoryScheduler.lease()`; `Worker._run_agent()`
    finally populates `RunMeta.tenant_id` from it (previously always `None`
    regardless of what was enqueued — the field existed end-to-end but
    nothing wrote it). `PostgresScheduler.lease()`'s claim query rewritten
    as `ROW_NUMBER() OVER (PARTITION BY tenant ORDER BY priority,
    enqueued_at)` — a tenant flooding the queue can no longer push another
    tenant's run down to a fixed low rank; both always compete for the next
    slot. `Supervision` (kernel) gained `to_dict()`/`from_dict()`;
    `ravi_run_tree.supervision` (JSONB) persists it at spawn time; new
    `Supervisor.supervision_of(run_id)` lets the Worker rehydrate it into
    `RunMeta.supervision` at lease time; `ctx.spawn()` without an explicit
    `supervision=` override now calls `self._meta.supervision.spawn_child()`
    when available instead of unconditionally falling back to
    `Supervision.root()` — `execution_budget` inheritance verified
    transitively (grandchild sees the budget a mid-tree spawn set, proving
    the Worker rehydrates it fresh at every lease, not just once).
  - **Phase 4** (partial — see deferred list above): `human_gate` gained
    `POST /hitl/request` (the create route never existed — `create_request()`
    was an unreachable service-layer function) and `resolve_request()`/
    `cancel_pending_for_thread()` now accept a `signal_bus` that fires the
    same `hitl:{request_id}` signal `AskHumanTool`'s signal-suspend path
    waits on, alongside the existing Redis pub/sub publish (both point at
    the same physical Postgres database — verified via the shared
    `DATABASE_URL` env var in both `docker-compose.microservices.yml` and
    the k8s secrets).
  - New tests: `tests/serving/test_bridge_registry.py` (new file, durable
    HITL fallback), `tests/serving/test_human_gate_service.py` (new file, 3
    tests), `tests/serving/test_session.py` (durable-cancel replacement for
    the removed `cancel_event` test), `tests/agents/test_runtime.py` +
    `tests/agents/test_runtime_postgres.py` (execution_budget inheritance),
    `tests/agents/test_runtime_postgres.py` (+6 more: single-flight ×2, fair
    scheduling, SSE cross-replica reconnect), `tests/integrations/
    test_redis_history.py` (+2: seed-lock exclusivity, concurrent-seed race).
    Full suite green (448 unit — one confirmed pre-existing, unrelated
    Redis-subscription-timing flake in `test_triggers.py` reproducible only
    under full-suite load, passes standalone — + 18 Postgres + 4 Redis + 3
    human_gate), 5/5 import-linter contracts, 10/10 kernel invariants.

- **Phase 1 PR8 — cleanup** (2026-07-03): signal GC — `finish_run()` now
  deletes every `ravi_signals` row addressed to a run the instant it goes
  terminal (both consumed and any never-claimed stragglers), and
  `InMemorySignalBus` got a matching `gc()` for the in-memory backend's
  `_buffered` dict. New `terminated_at` column on `ravi_run_queue` (set by
  every path that lands a run in a terminal state: `release()`,
  `PostgresSupervisor.cancel()`'s suspended-terminal-mark,
  `PostgresScheduler.lease()`'s deadline-fail) backs a new
  `infrastructure/runtime/retention.py::sweep_terminal_runs(pool,
  older_than=...)` — not wired into any automatic loop, callable from an ops
  cron job, deletes `ravi_event_log`/`ravi_signals`/`ravi_spawn_effects`/
  `ravi_run_tree`/`ravi_agent_runs`/`ravi_run_queue` rows for old terminal
  runs (deliberately does not touch `ravi_inbox` — it's agent-scoped, not
  run-scoped, so there's no safe way to delete by run_id without risking a
  live message for that agent's next run). Fixed two dangling `Checkpoint`
  docstring references in `kernel/core/errors.py` and
  `kernel/runtime/log_entry.py` (no such type has ever existed in this
  codebase — replaced with the actual fold/EffectCache mechanism) and a
  stale `Supervisor` cancellation-cascade docstring describing the
  pre-PR7 wakeup-message design. Added 7 new decision records to
  `decisions.md` (SuspendInterrupt/replay-from-top, path-derived
  determinism, all-Postgres coordination, heartbeat-based cross-process
  cancel) and rewrote `architecture/runtime-stages.md`'s "known gap" section
  — it described exactly the durability hole PR4-PR7 closed. New tests:
  `test_pg_signal_gc_on_finish`, `test_pg_retention_sweep`. Full suite green
  (436 unit + 13 Postgres integration), 5/5 import-linter contracts, 10/10
  kernel invariants — Phase 1 is now fully shipped.
- **Phase 1 PR4-PR7 — durable coordination core, completed** (2026-07-03):
  the heart of the program — `SignalBus`/`Supervisor` coordination moved
  fully off in-process memory onto Postgres.
  - **PR4**: `PostgresSignalBus` (`infrastructure/runtime/pg_signal_bus.py`)
    — buffered `ravi_signals` table, `consumed_by=effect_id` exactly-once
    fencing, `FOR UPDATE SKIP LOCKED` claim. `ravi_run_queue` gained
    `wake_signals`/`wake_at`/`cancel_requested`/`deadline` columns (additive
    migration, run *after* `_CREATE_TABLES` since that's a no-op on an
    existing table). `release(SUSPENDED)` double-checks `ravi_signals` in
    the same transaction before parking, closing the lost-wakeup race.
    `InMemorySignalBus` rewritten to matching consume-based semantics
    (`Wakeup.signal` → `Wakeup.signals: list[str]`).
  - **PR5**: Worker catches `SuspendInterrupt` (a `BaseException`) and
    releases the lease as `SUSPENDED` — the Task genuinely ends (zero
    RAM/CPU), not a blocked coroutine. `inbox.drain` is now a journaled
    effect so a message arriving mid-suspension can't nondeterministically
    change a replay's `inbox_msgs`. `sleep_until_signal`/`sleep_until`/
    `ask`/`reply` rewritten on `consume()`. Found and fixed 3 replay-
    determinism bugs along the way, all sharing one root cause (agent
    `run()` code constructing fresh `Message`s with fresh auto-generated
    ids on every replay attempt is incompatible with replay-from-top) and
    one fix pattern (derive identity from `RunContext._alloc_path()`,
    never from message fields or freshly-computed values): `ask()`'s
    correlation_id, `InMemorySupervisor.spawn()`'s effect_id, and a
    `spawn()`+`ask()` double-delivery collision via the Inbox's msg-id
    dedup (fixed with `RunHandle.boot_correlation_id`).
  - **PR6**: `PostgresSupervisor` (`infrastructure/runtime/pg_supervisor.py`)
    — `ravi_run_tree` (parent/root/status) + `ravi_spawn_effects` (spawn
    idempotency keyed by the caller's replay-stable effect_id, mirroring
    `InMemorySupervisor`). `finish_run()` is the new Protocol method
    (replaces `record_completion` in both backends): marks the run
    terminal and fires a `child:{run_id}` signal to the parent in one
    call. `Runtime.__init__` takes an injected `supervisor` (added a
    public `Runtime.supervisor` property alongside the existing
    `event_log`/`inbox`/`signal_bus`). `ctx.join()` rewritten off
    `Supervisor.join()` (asyncio.Event blocking — the one remaining
    kernel-contract-violating wait) onto the same consume-based
    `child:{run_id}` signal `ask()` already used — a miss raises
    `SuspendInterrupt` like every other suspension primitive.
  - **PR7**: cancel cascade via a recursive CTE in
    `PostgresSupervisor.cancel()` — seeds with the handle's own run_id
    unconditionally (a top-level `submit()` run has no `ravi_run_tree` row
    at all; only `ctx.spawn()`'d runs do) then walks `parent_run` down.
    Pending/running runs in the subtree get `cancel_requested=true`
    (observed at the next heartbeat, ≤15s); suspended runs have no live
    task to ever heartbeat, so they're terminal-marked directly via
    `finish_run()`. `Scheduler.heartbeat()` now returns `bool` (kernel
    Protocol change) — `True` means a durable cancel or deadline was
    observed, and the Worker cancels the run's local
    `CancellationToken` in response (this is how a cancel issued by a
    *different* worker process reaches a live Task, since only the
    leasing worker holds it). Deadline enforcement lives in
    `PostgresScheduler.lease()`'s existing poll: a pending/suspended run
    past its `deadline` column terminal-fails directly (nothing will ever
    heartbeat it); `Scheduler.enqueue()` gained an optional `deadline`
    param to set it. The `ask()` crash fast-path (child fails →
    `child:{run_id}` signal → parent's `ask()` returns `target_failed`
    immediately) was already wired in PR5/PR6; PR7 added dedicated test
    coverage for it.
  - New tests: `tests/agents/test_runtime_postgres.py` grew from 6 to 11
    (spawn+join, join crash fast-path, cancel cascade across a 2-deep
    spawn tree, deadline enforcement via direct SQL write, ask() crash
    fast-path via a spawned `RunHandle`). Full suite green (434 unit + 11
    Postgres integration), 5/5 import-linter contracts, 10/10 kernel
    invariants.
  - **Known scope boundary carried to PR8/Phase 2+**: the `deadline`
    column has no writer yet (`ExecutionBudget.deadline_s` is still not
    threaded through `ctx.spawn()` — same pre-existing gap the audit
    flagged); the scheduler-level deadline-fail path also doesn't notify
    a joining parent (no Supervisor reference from `PostgresScheduler`) —
    a parent relying on that instead needs its own `ask()`/`join()`
    timeout, which is unaffected and already correct.
- **Phase 1 PR3 — event-log-as-journal** (2026-07-02): `EffectCache`
  (`agents/runtime/effect_cache.py`) folds a run's `effect.result` EventLog
  entries into an in-memory dict once per lease — this is `fold()` for real,
  not just a docstring promise. `RunContext` no longer uses the Journal for
  effect dedup: `_record_effect()` appends `effect.result` to the EventLog
  (durable — the full LLM response content is now captured, not just token
  counts) and updates the cache; `_lookup_effect()`/`_resolve_effect_value()`
  read it back, with values >64KB offloaded to `BlobStore` and referenced by
  `artifact_ref` (lazily dereferenced only on a genuine cross-process replay
  hit). `RunContext` also gained a local `_seq_cursor` (seeded from the
  fold's `last_seq`), removing the per-append `last_seq()` query and turning
  `ConcurrentAppendError` into real zombie-worker fencing. `RedisJournal` was
  removed from `build_postgres_runtime` (dropped `redis_url`/
  `journal_ttl_seconds` params) — Redis is no longer in the effect-durability
  path, closing the TTL-expiry gap where a long-suspended run used to come
  back to a journal miss on every effect (LLM calls re-billed, tools
  re-executed). Found and fixed a real bug during this work:
  `InMemorySupervisor.spawn()` appends `child.spawned` directly to the
  *parent's own* EventLog, bypassing `RunContext`'s new cursor — surfaced as
  `ConcurrentAppendError` failures across all of `tests/fabric/test_flows.py`;
  fixed by resyncing the cursor in `RunContext.spawn()` after the supervisor
  call returns. New test file `tests/agents/test_effect_cache.py` (10 tests):
  fold reconstruction, crash-and-replay for both `uuid()` and `llm()` (proves
  no re-billing), artifact offload + lazy resolve, and zombie-fencing.
  Full suite green (428 passed), 5/5 import-linter contracts, kernel
  invariants pass.
- **Phase 1 PR2 — hierarchical effect paths** (2026-07-02): `RunContext`
  replaced the flat `_step_seq` counter with a scope-stack (`_alloc_path` /
  `_enter_scope` / `_exit_scope`); `Effect.make_id` now takes a hierarchical
  `path: str` instead of a flat `step_seq: int` (kernel contract change).
  `tool()` opens a child scope only on a genuine journal miss (never on a
  cache hit), so a tool's internal journaled calls (e.g. a future
  `ctx.uuid()`-derived `ask_human` request_id) no longer desync sibling
  effect ids when the tool call itself replays as a hit. New regression test:
  `tests/agents/test_runtime.py::test_nested_effect_inside_journal_hit_tool_stays_replay_safe`.
  `SuspendInterrupt(BaseException)` added to `kernel/core/errors.py`, unused
  until PR5. Prerequisite for PR3-PR5; in-memory behavior otherwise
  unchanged, full suite green (412 passed) incl. Postgres runtime tests.
- Signal-based "dead" HITL for `ask_human` (console + monolith web):
  `sleep_until_signal` suspend, Skip-always-offered, cancel-and-resubmit,
  per-call timeout exemption for suspending tools (`suspends = True`),
  card reconstruction on reload via `_card` embedded in `tool_result`.
- Task-board turn anchoring via `TaskList.created_at` (kernel + both stores).
- Relaxed `manage_tasks` keyword forcing; `AskHumanTool` option-quality
  guidance (no templated/placeholder option labels).
- Kernel audit (2026-07-02) + system architecture audit (2026-07-02) +
  `docs/claude_docs/` knowledge base established.
