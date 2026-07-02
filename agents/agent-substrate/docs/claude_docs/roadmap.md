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
| **2** | Horizontally scalable serving: scheduler-enforced single-flight (kill `thread_locks`); cancel via durable signal (kill `cancel_registry`); HITL cross-replica via PostgresSignalBus (kill `WebHITLBridge._pending` futures, incl. tool-approval migration); SSE-from-any-replica verification; memory-seed idempotency + partial-eviction reseed | Pending |
| **3** | Full multi-tenancy: `tenant_id` through thread/step queries (evaluate RLS), history keys, task store; per-tenant fair scheduling in `lease()`; wire `Supervision.execution_budget`/`spawn_child()` (currently dead code); tenant quotas + tenant-scoped rate limits | Pending |
| **4** | Microservices as the scale path: converge `human_gate` onto the Phase-1 signal bus + wire `agent_runtime` HITL pause/resume (today zero callers); feature-parity porting (files/RAG → triggers/scheduled incl. durable APScheduler job store → pipelines → MCP apps); k8s replica policies | Pending |
| **5** | Enterprise hardening: attach `AgentTracingMiddleware`/`ChatTracingMiddleware` (exist, never installed); webhook idempotency keys + HMAC; agent versioning guard on replay; event-log retention/compaction (implement snapshot only if fold P99 demands it) | Pending |

Verification gates per phase are specified in the plan (crash/replay harness,
two-worker integration, lost-wakeup race, cancel/deadline cascade, tenancy
isolation, global log invariants). Standard per-merge gates: `uv run ruff
check .` · `uv run pytest` · `uv run lint-imports`.

## Carried-forward items not in the program (opportunistic)

- **`agents/core/react.py` `_react()` refactor** (~150 lines; extract
  guardrail + tool-concurrency helpers) — do when touching that file.
- **Connectors framework** (user-managed integrations via MCP, replaces
  hardcoded `spotify_oauth.py` pattern) — not started, post-program.
- **Docs debt**: root `CLAUDE.md` HITL section + mkdocs pages still describe
  pre-signal HITL; root CLAUDE.md tech-debt table still calls TaskStore
  in-memory-only (stale — `PgTaskStore` is live under Postgres backend).
- **Test coverage gaps** outside the program's new suites: guardrails,
  middleware, MCP adapter, `fabric/evals`.

## Recently shipped (prune over time)

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
