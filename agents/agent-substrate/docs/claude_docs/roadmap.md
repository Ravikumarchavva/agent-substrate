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
   transactional with EventLogProtocol + scheduler). EventLogProtocol becomes the single
   source of truth for effect results; Redis journal leaves the correctness
   path. Suspension = `SuspendInterrupt` unwind + replay-from-top with
   journal fast-forward. Hierarchical effect paths replace flat `_step_seq`.
   No backward compatibility.

## Phase status

| Phase | Content | Status |
|---|---|---|
| **0** | Audit record; IDOR fix (thread ownership); stable advisory-lock key; rate-limiter fail-closed | **Done** (2026-07-02) — `get_owned_thread` + ownership on chat/cancel/hitl-status/threads/tasks/mcp-context; ownership stamped at creation; list scoped per user; legacy NULL-owner threads claim-on-first-access; sha256 `_lock_key`; `RATE_LIMIT_FAIL_OPEN=False` default (503 when Redis down); tests in `tests/serving/test_thread_ownership.py` |
| **1** | Durable coordination core: hierarchical effect paths (**PR2 done**) → event-log-as-journal + fold (**PR3 done**) → SignalBus + scheduler columns (**PR4 done**) → durable suspend/resume via `SuspendInterrupt` (**PR5 done**) → Supervisor (**PR6 done**) → cancel cascade + deadlines + crash fast-path (**PR7 done**) → cleanup/GC/docs (**PR8 done**) | **Done** (2026-07-03) — all 7 PRs shipped; Phase 2 (horizontally scalable serving) is next |
| **2** | Horizontally scalable serving: scheduler-enforced single-flight (kill `thread_locks`); cancel via durable signal (kill `cancel_registry`); HITL cross-replica via SignalBus; SSE-from-any-replica verification; memory-seed idempotency | **Done** (2026-07-03) — see "Recently shipped". Tool-approval durability/wiring: see the "Explicitly deferred" entries below — the 2026-07-12 kernel audit found this was worse than "Future-based," it wasn't wired to a live agent at all; now wired (kernel-Protocol-based), still Future-based (signal migration still open) |
| **3** | Full multi-tenancy: `tenant_id` through thread ownership + `RunMeta`; per-tenant fair scheduling in `lease()`; wire `Supervision.execution_budget`/`spawn_child()` (was dead code) | **Done** (2026-07-03) — see "Recently shipped". `RedisHistoryProvider`/`GlobalTaskStore` tenant-keying and tenant-level quota aggregation/rate limits explicitly deferred (see below). **Correction (2026-07-12, kernel audit):** "wire execution_budget" here only ever meant the *propagation* half (`Supervision.spawn_child()` correctly threading the field through) — the *enforcement* half (actually building an `ExecutionTracker` from it for the spawned child) was still dead code until the kernel audit's Tier B fixed it. See `docs/claude_docs/kernel/2026-07-12-audit.md`. |
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
- ~~`SpawnBudget` enforcement was fictional~~ — **Done** (2026-07-18,
  durable-agent-developer audit). Kernel docstrings (`kernel/runtime/
  supervisor.py`, `kernel/agent/supervision.py`) described a durable,
  `SpawnDenied`-raising enforcement point at `SupervisorProtocol.spawn()` that
  didn't exist in code — no such exception class ever existed either (the
  real one, already in use for `ExecutionTracker`, is
  `BudgetExhaustedError`; docstrings now say so). The only real enforcement
  was `SpawnTracker.acquire()`/`release()`, an in-process,
  per-`OrchestratorAgent`-instance convention — any other code calling
  `ctx.spawn()` directly bypassed the headcount cap entirely. Fixed by
  moving the check into `Supervisor.spawn()` (advisory-lock-
  serialized against a concurrent-spawn race, mirroring `EventLog`'s
  own check+insert pattern) and `InMemorySupervisor.spawn()`, so the budget
  applies regardless of caller. `SpawnTracker`'s cooperative-preemption
  half (`is_paused()`/`reprioritize()`) is real and tested but its own
  docstring's claim that an agent loop checks it "before each LLM call" was
  false — no loop in this codebase does; docstring corrected, methods kept
  (real, useful bookkeeping, just not auto-consumed yet). Tests:
  `tests/agents/test_spawn_budget_enforcement.py` (in-memory),
  `test_pg_spawn_denied_once_headcount_cap_reached`
  (`tests/agents/test_runtime_postgres.py`, real Postgres).
- ~~`agents/supervision/policies.py::RetryPolicy` was dead code~~ — **Done**
  (2026-07-18). In-memory, per-process, no-backoff retry counter with zero
  real callers anywhere (confirmed via grep — not even its own test;
  `tests/agents/test_retry.py` tests `RunRetryPolicy`, the actual durable
  mechanism, despite the similar name). Deleted the class and its
  `__all__` export; `agents/supervision/__init__.py` now points readers at
  `RunRetryPolicy` instead.
- **`ctx.tenant_id` is set but never read anywhere in the execution layer**
  (found 2026-07-18, durable-agent-developer audit, not previously
  flagged). `RunContext.tenant_id` is correctly plumbed from JWT claims
  through `SchedulerProtocol.enqueue`/`Lease.tenant`/`RunMeta.tenant_id`, but
  nothing inside an agent's own execution (tools, history/memory access)
  ever reads it — isolation today is fully enforced upstream, at the
  thread-ownership/route layer (`get_owned_thread`), which is sufficient
  (no cross-tenant leak path found). Not urgent, but the field is
  currently decorative inside a run; either consume it somewhere real
  (tenant-scoped tool/resource access) or document plainly that
  execution-layer code must not assume it does anything on its own.
- **`InMemorySupervisor`/`Supervisor` cancel semantics diverge**
  (found 2026-07-18). `InMemorySupervisor.cancel()` is immediate/forceful —
  no cooperative-heartbeat wait, no `cancel_requested` round-trip.
  `Supervisor.cancel()` is cooperative with a real ≤15s latency
  bound (heartbeat interval). A test written and passing only against the
  in-memory backend cannot catch a real cooperative-cancel-latency
  regression. Not a bug — Stage-0 in-memory has no concurrent-lease
  scenario to race against — but worth knowing before trusting an
  in-memory-only cancel test as proof of production cancel behavior.
- **Dual retry counters (scheduler retry vs. inbox nack) — unverified**
  (found 2026-07-18). The 2026-07-02 audit's M4 finding described
  independent retry counters over the same underlying failure; the
  2026-07-05 v1 remediation fixed the backoff/ordering half but didn't
  explicitly confirm the dual-counter aspect closed. Needs a targeted grep
  on `Lease.attempt` and the inbox nack path before triaging further —
  not done as part of this audit (out of scope; flagged for a future
  session).

## Explicitly deferred from Phase 2-4 (scoped out, not forgotten)

Each of these was evaluated during Phase 2-4 implementation (2026-07-03) and
deliberately scoped out — either because it's a substantially larger feature
than the surrounding architecture fix, or because the real risk it addresses
turned out to be negligible given how the code actually works. Recorded here
so the decision is visible, not silently dropped.

- ~~Future-based tool-approval → signal migration~~ — **superseded**
  (2026-07-12, kernel audit — see
  `docs/claude_docs/kernel/2026-07-12-audit.md` Tier C). This entry assumed
  tool-approval worked but used Futures instead of signals; the actual
  finding was worse — it wasn't connected to a live agent at all (three
  independently-built approval abstractions, none wired to `ReActAgent` in
  the real construction path, so a CRITICAL-risk tool call executed
  completely unguarded). Fixed by consolidating on the kernel
  `ApprovalHandler` Protocol and wiring `SSEApprovalHandler`
  (`serving/monolith/sse/approval.py`) through
  `build_agent_for_thread(bridge=...)`. Still Future-based (`WebHITLBridge.
  request_and_wait()`), not signal-based — that migration (to survive a
  process restart mid-approval, matching how `ask_human` already works) is
  still open and now tracked as its own item below, not blocking on
  "connect it at all" anymore.
- ~~**Tool-approval: Future-based → signal-based**~~ — **Done** (2026-07-18,
  durable-agent-developer audit). `ToolInvoker._invoke_inner`'s approval
  branch now suspends via `ctx.sleep_until_signal(f"hitl:{request_id}")`
  exactly like `ask_human`, reusing the identical signal namespace so
  `WebHITLBridge.register_signal_request()`/`resolve()` needed zero new
  methods — only `serving/stream/session.py`'s tailing loop and
  `routes/hitl.py`'s cold-restart card reconstruction gained an
  `ApprovalRequestedEvent`/`"approval.requested"` branch alongside their
  existing `InputRequestedEvent`/`"input.requested"` one.
  `SSEApprovalHandler.request()` (Future-based) survives only as a fallback
  for a handler constructed with no `signal_bus`. Proven durable against a
  fully closed-and-reopened `asyncpg` pool (not just a discarded Python
  object) in `test_pg_tool_approval_survives_full_pool_close_and_reopen`
  (`tests/agents/test_runtime_postgres.py`), plus 3 in-memory unit tests
  (`tests/agents/test_tool_approval_durability.py`). Stale `# TODO: L4-hitl`
  markers removed from the three risk-gated tools that prompted this —
  they needed no code changes themselves, since the fix lives entirely in
  the invoker/bridge layer.
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
- **Feature-parity porting** (files/RAG → triggers/scheduled → pipelines →
  MCP apps) and **k8s replica-policy tuning**. Both are explicitly called out
  in the original plan as a long tail — genuinely multi-week feature/ops
  work, not architecture remediation. Per-service k8s manifests already
  exist (`deployment/k8s/base/runtime/*.yaml`) and are not a monolith-only
  deployment; nothing here blocks scaling replicas today.
  **Update (2026-07-05, v1 remediation):** moving `TriggerScheduler` off
  `MemoryDataStore` is no longer just "long tail" — it's actively blocked.
  The Makefile's `PYSEC-2026-282` (apscheduler RCE) ignore is justified
  entirely by "we only ever construct `MemoryDataStore`"
  (`capabilities/triggers/scheduler.py` module docstring); switching to a
  persistent job store to fix multi-replica duplicate-firing would reopen
  that CVE. A real fix needs the CVE resolved (or a from-scratch trusted-
  deserializer patch) first. `tests/capabilities/test_triggers.py::
  test_scheduler_uses_memory_data_store_not_a_persistent_one` fails loudly
  if anyone changes the data store without addressing this. Until then:
  **do not run more than one replica of a process that calls
  `TriggerScheduler.start()`** — every replica runs its own independent copy
  of every schedule, so a cron trigger fires once per replica, not once
  total. This is documented in code, not fixed.
- **Event-log snapshot/compaction (`Checkpoint`).** Evaluated during Phase 5
  (2026-07-04): `sweep_terminal_runs` (`infrastructure/runtime/retention.py`,
  shipped in PR8) already handles retention — deleting EventLogProtocol/signals/
  tree/spec/queue rows for old *terminal* runs. Compaction (folding a
  long-lived run's effect history into a snapshot so replay doesn't refold
  from entry 0) is the genuinely unbuilt half, but the plan's own guidance is
  explicit: build it "only if measured fold cost exceeds budget at P99" — no
  such measurement exists yet. Building a snapshot format/versioning scheme
  speculatively is exactly the kind of premature abstraction the project's
  coding standards warn against. Revisit if fold latency is ever profiled
  and found to matter.
  **Update (2026-07-05, v1 remediation):** the stakes of this changed —
  `sweep_terminal_runs` now deletes a thread's *only* copy of its
  conversation history, not just internal coordination rows, because the
  EventLogProtocol is the sole source of truth for both (see the "Persistence
  collapsed onto the EventLog" entry below). The function's docstring
  carries an operational warning, but nothing *enforces* a minimum
  retention window or warns an operator before they run it against threads
  users still expect to reload. Needs either a configurable minimum-age
  floor with a sane default, or an explicit "this deletes chat history"
  confirmation in whatever calls it in production — not addressed yet.
- **History-replay pagination/snapshotting for `project_thread()` /
  `step_rows_from_log()`.** Both (`serving/stream/history.py`,
  `agents/factory.py`) replay a thread's *entire* EventLogProtocol — every run, every
  entry — from scratch on every history load, reconnect-cold-start, and
  memory-seed. Fine at today's scale; for a thread with hundreds of turns
  this is unbounded work on every page load with no pagination, incremental
  cache, or snapshot. Not measured, not fixed — flagged during the v1
  remediation session (2026-07-05) as a scalability gap the "single source
  of truth" redesign introduced without a mitigation. Revisit if thread
  history load time is ever profiled and found to matter (same "measure
  first" standard as event-log compaction above).

- **Microservices event architecture: mostly unbuilt past job start/fail.**
  Found 2026-07-12 via a new AST-based dead-symbol scanner
  (`scripts/find_dead_symbols.py`) run against the whole codebase, not just
  kernel. Of `serving/shared/events/types.py`'s ~28 domain-event factory
  functions, only 3 have a real producer anywhere: `session_started`
  (`identity/service.py`), `workflow_started` and `workflow_failed`
  (`job_controller/service.py::dispatch_run`). The other 25 — including
  `workflow_completed`/`workflow_cancelled`, every `agent_*` streaming
  event, every `tool_call_*`/`hitl_*` event, `thread_created`/
  `message_persisted`, `task_*`, `artifact_*` — are defined but never
  called. `live_stream` (the SSE projector service, per this doc's own
  services table) exists to turn these into real-time client updates the
  same way the monolith's SSE bridge does; with 25 of 28 producers missing,
  a microservices-deployed chat session gets essentially no real-time
  updates beyond a run starting or hard-failing — no streaming text, no
  visible tool calls, no HITL cards, no thread/message persistence signal.
  Concretely blocking on this: **`job_controller::complete_run` is dead
  because nothing ever publishes `workflow_completed`** — not
  `job_controller` itself, not `agent_runtime` (which per `dispatch_run`'s
  docstring is supposed to "execute the agent, publishing progress events
  back," but doesn't). A run that finishes successfully in the
  microservices deployment has no code path that ever marks it
  `completed` — it either fails explicitly or stays `running` forever.
  This is not a "wire up an existing handler" fix like the kernel audit's
  tool-approval finding; it requires designing what `agent_runtime`
  publishes at each step of its own run loop first. Scoped out of the
  2026-07-12 investigation as multi-service, multi-day work — this is the
  single largest concrete gap between "microservices exist" and
  "microservices are feature-complete with the monolith." Two small,
  unambiguous pieces of the same investigation *were* fixed directly (see
  "Recently shipped"): `admin::write_audit_log` wired into
  `create_tenant_endpoint` (previously the audit log was read-only — no
  action ever wrote an entry), and `tool_executor::execute_and_publish`
  wired into `POST /tools/execute` (previously always called bare
  `execute_tool`, so `tool.execution_completed` never fired even though
  the publishing code existed and was correct).

- **Real `Instance` (deployed-chatbot) provisioning in `ravi`** (found
  2026-08-15, cross-repo investigation while wiring a `tenant_id`
  identity/quota seam between `ravi` and `substrate-ui`). `ravi`'s
  `Instance` model (`prisma/schema.prisma`) has real-looking fields —
  `port`, `containerId`, `healthUrl`, `externalUrl` — but nothing in `ravi`
  ever populates them. A `CHATBOT` `Instance` row is auto-seeded at
  `status: STOPPED` the first time a project's dashboard is viewed
  (`project-catalog.ts::ensureProjectBlueprint`, called from three places,
  none a user "deploy" action), and the chatbots management page
  (`chatbots/page.tsx`) is entirely read-only — its own copy admits this
  ("...and **later** point at a provisioned chat UI instance"). Repo-wide
  search for Docker/k8s/`spawn`/`exec`/`provision` in `ravi/src`: zero hits.
  **There is no code anywhere that spins up a `substrate-ui` container/pod
  per chatbot.** Building this is a substantial infra project (container or
  k8s pod orchestration, health-check polling, `externalUrl` assignment,
  teardown) — scoped out of the identity-seam work specifically so it could
  land without waiting on this. What *did* ship as groundwork: `ravi`'s
  `createEngineToken` and `substrate-ui`'s `engine-auth.ts` both now support
  an optional project-scoped `tenant_id` claim (`RAVI_PROJECT_ID` env var on
  the substrate-ui side) — so once real provisioning exists, it's "set one
  env var on the deployed container," not another auth rewrite. A real
  deploy action also needs to precede this: today even the *manual* "point
  at a provisioned chat UI instance" step doesn't exist as a UI affordance.
- **Project-scoping in the `ravi` builder** (found the same session as
  above). The visual pipeline builder (`dashboard/builder/page.tsx`) and
  its API client (`builder-api.ts`) have no project-selection concept at
  all — no `projectId` in any call, despite `Workflow.projectId` existing
  as a real FK in the schema. This blocked passing a real `tenant_id` into
  the builder's own test-chat proxy (which now sends a real per-user JWT
  instead of the completely unauthenticated request it sent before, but
  still falls back to `tenant_id: "default"`, not a real project). Fixing
  this means deciding how a user picks/switches which project they're
  building for, which is a real UX design question, not implemented here.

## Recently shipped (prune over time)

- **`ravi` plan/BYOK model + passwordless auth + `tenant_id` identity seam +
  self-hosted error tracking** (2026-08-15, same session as the safety
  guardrail below). `ravi`'s `Plan` enum went from a fictional
  FREE/PRO/ENTERPRISE billing-tier list (nothing enforced it, no payment
  processor wired up) to FREE/EXPLORER — FREE capped at 5 messages/day
  against Ravi's own credentials, EXPLORER unlocked by connecting your own
  LLM API key (`Project.byokProvider`/`byokKeyEncrypted`, AES-256-GCM at
  rest via `src/lib/byok-crypto.ts`) and capped at 100/day. Enforcement is
  real: `agent-substrate`'s `chat.py` checks a Redis daily counter
  (`plan_quota_key()`, reusing the existing `doc_quota.py` primitive) keyed
  by a `daily_message_limit` JWT claim `ravi` embeds from `Project.plan`.
  Auth went fully passwordless — `next-auth`'s first-party `resend`
  provider (no nodemailer needed), `Credentials`/`passwordHash` deleted
  outright, `/login` and `/register` unified into one "enter your email"
  flow. The builder's own test-chat proxy went from **zero auth at all**
  (a bare `next.config.ts` rewrite) to a real per-user signed JWT. A new
  `tenant_id` identity seam now runs through both `ravi`'s
  `createEngineToken` and `substrate-ui`'s `engine-auth.ts` (env var
  `RAVI_PROJECT_ID`) — inert until real Instance provisioning exists (see
  the "Explicitly deferred" entry above) but means that, once it does, a
  deployed chatbot's entire visitor base (anonymous or logged-in) shares
  one project-level quota by setting a single env var, not another auth
  rewrite. Error tracking: self-hosted GlitchTip (MIT, Sentry-protocol-
  compatible — `deployment/docker/docker-compose.yml`'s `glitchtip-*`
  services, `profile: glitchtip`) wired into both `ravi` and `substrate-ui`
  via `@sentry/nextjs`, client-side only (server-side errors already flow
  into the existing Promtail→Loki path). Also shipped: real Terms/Privacy
  pages (previously 404 in production).
- **Multimodal input safety guardrail** (2026-08-15). The regex-only
  guardrails in `agents/middleware/guardrails/` had existed for a while but
  were never actually wired into the live chat path — confirmed via grep
  that `build_agent_for_thread()` never passed `middleware=[...]` into
  `create_assistant_agent()`. New `MultimodalSafetyMiddleware` fixes that:
  runs on every chat turn, group-evaluates text (normalized + regex +
  `Llama-Prompt-Guard-2-86M` via onnxruntime) and any directly-attached
  image (`OwenElliott/image-safety-classifier-xs`), and — the genuinely new
  mechanism — persists a flagged message in thread history (visible, badged
  via a new `MessageFlaggedEvent`) while excluding its raw text from every
  *future* turn's LLM context (`agents/factory.py::step_rows_from_log`'s
  redaction pass). See [`decisions.md`](decisions.md) for why
  `Opir-edge-multilang` (the original one-model target) was rejected after
  real measurement, and the licensing tradeoff that decision reopened.
  Also shipped: `capabilities/safety/document_scanner.py` (thin wrapper
  over `doc-firewall`, verified against both a clean and a synthetically
  malicious PDF) gating the extraction service's `/v1/extract` — runs on
  raw bytes before PaddleOCR/PaddleX ever parses them — and paste-to-
  document (a composer paste over ~2000 chars becomes a `text/markdown`
  attachment through the *existing* upload/staging pipeline, no new
  endpoint). New kernel contracts: `kernel/agent/safety.py`
  (`SafetyVerdict`, `Severity`, `TextSafetyClassifier`/`ImageSafetyClassifier`
  Protocols). New agents-layer module: `agents/safety/normalize.py` (NFKC +
  UTS-39 confusables-skeleton homoglyph defense — deliberately NOT folded
  into kernel, and deliberately not left in `capabilities/` after a real
  import-linter violation caught it there first, since an L1 middleware
  needs it too and L1 can't import L2).
- **Whole-codebase dead-symbol scan** (2026-07-12, same day as the kernel
  audit below, follow-on session). New `scripts/find_dead_symbols.py`
  (`make audit-dead-symbols`) — AST-based, module-level only, deliberately
  not method-level after a knowledge-graph tool (graphify) evaluated the
  same day was shown to false-positive on real live code
  (`Supervision.spawn_child`) via incomplete cross-file method resolution.
  Two tuning passes against real output cut false positives 259 → 58 → 54:
  followed `import X as _X` aliases (common in the LLM client modules),
  skipped decorator-dispatched defs (FastAPI routes, auto-discovered
  tools), and treated any string-literal match as a reference (catches
  SQLAlchemy's `Mapped["User"]`/`relationship("User")` string forward-refs,
  which don't produce an `ast.Name`). Confirmed findings deleted:
  `integrations/llm/encoders/storage.py` (whole file — zero importers,
  `postgres_history.py`/`redis_history.py` each independently reimplement
  the same serialize/deserialize logic instead of using it),
  `mcp_apps.py::resolve_ui_uri` (resolves a `ui://` URI nothing ever
  produces — all UI resources are pre-registered by static file-scan),
  `identity/service.py::get_user_by_id` (no route ever needed it — `GET
  /auth/me` reads JWT claims directly). Two half-wired bugs of the same
  shape as the kernel audit's tool-approval finding were fixed directly:
  `admin::write_audit_log` and `tool_executor::execute_and_publish` (see
  the deferred item above for the third, much bigger one —
  `job_controller::complete_run` / the microservices event architecture —
  which was scoped out as multi-service work, not fixed). Left as a manual
  target, not CI-gated; the 54 remaining CONFIRMED and 86 SUSPECT symbols
  from this pass haven't all been reviewed by hand yet.
- **Kernel foundation audit** (2026-07-12 — full findings in
  `docs/claude_docs/kernel/2026-07-12-audit.md`; requested directly by the
  user as a ground-up rethink of `kernel/` after the v1 remediation program,
  on the hypothesis that rot above kernel kept recurring because kernel
  itself wasn't rigorously curated). Every symbol `kernel/__init__.py`
  exports traced against real call paths, not import counts. Deleted ~10
  fully dead contracts (kernel shrank 5295 → ~4840 LOC): a whole parallel
  `Event`/`EventPublisher`/`EventSubscriber` pub/sub contract superseded
  before ever being implemented by `integrations/events/envelope.py`'s
  independently-built `EventEnvelope`; a `ToolSpec`/`spec_of()` tool-spec
  encoding taxonomy the one real consumer (`Toolbox.schemas()`) was never
  migrated to use; `ThinkingBlock`/`UIResourceBlock` content-block types no
  producer ever emits; three error classes never raised; two dead
  extension-registration hooks. Fixed `Supervision.execution_budget`, which
  propagated through spawn but was never converted into an enforced
  `ExecutionTracker` for the child. **Headline finding:** tool-approval had
  three independently-built, unconnected implementations (kernel Protocol,
  a capabilities module, an SSE bridge with real `hitl_mode` logic) — traced
  end to end and confirmed a CRITICAL-risk tool call was not gated on human
  approval anywhere in the live monolith. Consolidated on the kernel
  `ApprovalHandler` Protocol (user-confirmed direction), added
  `ApprovalDecision.MODIFIED`, deleted the other two implementations, wired
  a new `SSEApprovalHandler` through the real `build_agent_for_thread()`
  construction path, and added an end-to-end test that exercises that real
  path rather than a hand-built agent (the gap that let the disconnect go
  unnoticed). Also found and fixed, incidentally: a genuine ~35s test
  "hang" (`test_supervision_v2.py`) that was actually correct exponential
  backoff behavior on an under-configured `max_retries` — pre-existing, not
  a regression, just never triggered by anyone running that one test in
  isolation before.
- **v1 remediation program** (2026-07-05 — separate from, and after, the
  Phase 0-5 program above; triggered by a pre-first-release production-
  readiness audit). Eight workstreams, all shipped and gated on `uv run
  ruff check .`/`lint-imports` 5/5/`pyright src/` 0 errors/full suite green
  (505 tests; one pre-existing Redis-timing flake in
  `test_condition_trigger_dispatch`, confirmed unrelated and passes in
  isolation) after every commit:
  - **Calculator RCE** — `capabilities/tools/compute/calculator.py`'s
    `eval()` (escapable via `().__class__.__base__.__subclasses__()`)
    replaced with a whitelisted-AST evaluator.
  - **Retry correctness** — `EffectCache.fold()` was rehydrating *error*
    effects as cache hits, so a scheduler retry replayed the same cached
    failure forever instead of re-executing (verified: 1 LLM call across 3
    "retries" before the fix). Fixed in `agents/runtime/effect_cache.py`;
    added exponential backoff (`RunRetryPolicy.max_backoff_s`) and
    retryable-vs-`PermanentError` classification (`kernel/core/errors.py`).
    Also fixed an ordering bug in `worker.py`: `run.failed`/
    `SupervisorProtocol.finish_run()` used to fire on the *first* transient error
    even when a retry was about to succeed, telling a parent's `ctx.join()`
    about a failure prematurely.
  - **Persistence collapsed onto the EventLog** — the largest piece. The
    `steps` table (a second, hand-written conversation store populated from
    the SSE connection) drifted from the EventLog on crash-mid-run: a run
    that resumed on another worker completed durably but its post-crash
    turns never reached `steps`. Deleted the `steps` table, the `Step`
    model, and all its readers/writers entirely. `user.message` is now a
    proper log kind (`agents/core/_loop.py::log_user_message`);
    `serving/stream/history.py::project_thread()` is the one canonical
    history projection powering live streaming, reconnect, AND the history
    endpoint; `agents/factory.py::step_rows_from_log()` is the sibling
    projection feeding agent-memory-seed through the same unchanged
    `rebuild_messages_from_steps()`. substrate-ui's `history-fold.ts` folds
    the same wire-event stream for history as it does for live SSE.
    Verified end-to-end by `test_pg_project_thread_survives_crash_and_resume`.
    **Known gaps this introduced, not yet closed** (see "Explicitly
    deferred" above for detail): retention (`sweep_terminal_runs`) now also
    deletes chat history, with no enforced minimum-retention floor; history
    replay has no pagination/snapshotting and re-folds the entire log on
    every load. `Feedback.for_id` was NOT re-anchored to a log-derived id as
    the original plan called for — investigation found it was never
    actually FK'd to `Step` and substrate-ui has zero live callers of
    `POST /feedbacks` right now, so the re-anchoring work was judged
    speculative and skipped; revisit if that endpoint gets a real caller.
  - **Journal retired** — `RedisJournal` (dead, never wired to a real path)
    and `InMemoryJournal` (only consumer was `InMemorySupervisor.spawn()`'s
    dedup) both deleted; spawn dedup now uses a plain dict, mirroring
    `Supervisor`'s own `substrate_spawn_effects` table. One
    durability primitive (EventLogProtocol), zero Journal.
  - **`Worker.cancel()` ownership bug** — it used to poke
    `scheduler._status` (a private attribute) and, whenever this worker held
    no local Task for a run_id, unconditionally force-append `run.cancelled`
    + call `SupervisorProtocol.finish_run()` — including for a run genuinely RUNNING
    on *another* replica, racing that replica's real completion. Added
    `SchedulerProtocol.cancel_pending(run_id) -> bool` (atomic, both backends) as a
    proper gate; only a non-RUNNING run gets force-terminalized locally now.
    Also fixed the `agent_runtime` microservice's cancel listener, which had
    the same gap (only called the local fast path, never the durable
    `SupervisorProtocol.cancel()` cascade).
  - **Dependency hygiene** — heavy tool stacks (`web`/`code`/`rag`/`s3`) moved
    to `[project.optional-dependencies]` extras; dead hard-deps
    (`markitdown-ocr`, `ipykernel`, `ipywidgets`, `pgvector`) deleted.
    **Caught one real mistake in this same pass**: `psycopg[binary]` was
    also deleted as "zero direct imports," but SQLAlchemy loads it
    dynamically for the monolith's `postgresql+psycopg://` DATABASE_URL —
    grepping for `import X` doesn't catch string/DSN-driven dynamic loading.
    Caught by `test_scheduled.py` failing against a real Postgres lifespan,
    not by the audit itself. See the decisions.md entry on dependency-audit
    methodology — the rest of this pass's removals were not systematically
    re-checked against the same blind spot.
  - **Public API + docs** — `Runtime.run()`/`Runtime.ask()` added as the
    ergonomic one-shot entry points the README now actually demonstrates;
    `substrate/__init__.py`/`agents/__init__.py` `__all__` curated to a
    stable v1 surface; README rewritten against the real API and gated by
    `tests/test_readme_examples.py` (executes every example against a stub
    LLM so it can't silently drift again).
  - **`ravi` → `substrate`/`agent-substrate` rename** — DB tables, indexes,
    NOTIFY channels, Redis key prefixes, CLI branding (`substrate
    start`/`stop`, single word), and a full `ravi-ui` → `substrate-ui`
    rename across both repos (directory, remote, CSS classes, localStorage
    keys, JWT claims — one malformed email artifact from an earlier partial
    rename fixed along the way).
  - **Operational metrics** — `substrate.runtime.retries`/`.suspensions` OTel
    counters added (`infrastructure/observability/runtime_metrics.py`),
    tagged by backend. **Only half the ask**: queue depth and lease age
    (arguably more useful for "is this falling behind") need periodic
    polling rather than a call-site increment and were deliberately not
    built in this pass — don't treat this item as fully closed.
  - **TriggerScheduler honesty** — see the updated "Feature-parity porting"
    bullet above; documented as single-instance-only with a CVE-guardrail
    test, not fixed.

  **Process note:** this was a large amount of change (kernel through
  serving, the DB schema, the dependency tree, two frontend repos) landed on
  one branch in one continuous session, self-verified by the same agent that
  wrote it. Justified by the explicit "no backward compatibility, break
  freely" pre-release mandate, but it has not yet had a human review pass —
  treat the branch as "believed correct, gated green" rather than "reviewed."

- **CI hardening + full architecture-boundary audit** (2026-07-05). `make ci`
  and `.github/workflows/ci.yml` had independent `|| true`/soft-fail bypasses
  on typecheck and security that silently defeated each other — both flipped
  to hard-fail, and a `lint-imports` target/CI job added (it was never wired
  in at all before). `uv run pytest --with pyright pyright src/` now runs
  clean (0 errors) as a gate. A dependency audit found the framework's own
  `pyproject.toml` pulled in `langchain`/`paddleocr[all]` transitively with
  zero real importers anywhere in `src/`/`tests/` — removed. Followed by a
  deep audit for out-of-boundary implementations (things living in the wrong
  layer) and duplicate logic, fixing:
  - `infrastructure/serving_factory.py` was importing backwards *from*
    `serving/` (forbidden direction) and embedding agent-topology
    construction that belongs in `agents/factory.py`. Fixed by adding
    `build_research_orchestrator()`/`build_token_budget_pipeline()` to
    `agents/factory.py` (accepting pre-built capability tools as parameters,
    since `agents/` cannot import `capabilities/` — caught by `lint-imports`
    on the first draft, which tried to construct `WebSearchTool` et al.
    directly inside `agents/factory.py`) and having `build_agent_for_thread()`
    accept an injected `cfg`/`load_persisted_steps` instead of importing
    `serving.shared.settings`/`serving.monolith.services` itself.
  - `_cosine_similarity` was reimplemented identically in three files
    (`capabilities/tools/ai/knowledge_search.py`, `agents/llm/cache.py`,
    `agents/storage/vector.py`). Consolidated into one
    `agents/storage/vector.py::cosine_similarity()`, re-exported from
    `agents.storage`.
  - All 9 microservices (`serving/services/*/app.py`) hand-rolled their own
    `aioredis.from_url()`/`.aclose()` instead of reusing
    `infrastructure/cache/redis.py::RedisConnector`. Switched all 9 to
    `RedisConnector`.
  - `serving/shared/contracts/human_gate.py`'s `HITLResponse.action` Literal
    only allowed `"approve"/"deny"/"modify"` (tool-approval actions) while the
    monolith's equivalent schema also allows `"answered"/"skipped"/"cancelled"`
    (human-input-resolution actions) — the microservices gateway would 422 on
    any human-input HITL response proxied through it. Aligned the two.
  - Several other audit candidates (trigger-scheduler→Runtime dispatch,
    tool-approval-list placement, MCP-apps static resource registry,
    `build_chat_tools`'s per-request tool composition) were investigated and
    found to be either legal under the enforced layering rules or too small
    to be worth the churn — left as-is rather than "fixed" for its own sake.
  Gate for all of the above: `uv run ruff check .`/`format --check` clean,
  `uv run lint-imports` 5/5 contracts kept, `uv run --with pyright pyright
  src/` 0 errors, full suite 441 passed.
- **Kernel dead-code cleanup — `Middleware`/`MiddlewareContextProtocol` deleted
  from kernel; `CancellationToken` split into Protocol + concrete impl**
  (2026-07-05, prompted by the user asking why middleware/agent/tool-approval
  Protocols were being defined in `agents/` when they "should all live in
  kernel"). Investigation found the kernel `Agent` Protocol genuinely has a
  real consumer (`fabric/evals/runner.py`) but kernel's `Middleware` Protocol
  and `MiddlewareContextProtocol` (added in the middleware rebuild below) had
  **zero** real consumers outside kernel's own re-export — every actual
  middleware implementation needs the concrete, richer `MiddlewareContext`
  dataclass's stage-specific fields, so a kernel-minimal duplicate Protocol
  was pure duplication. Deleted both; `kernel/agent/middleware.py` now
  defines only `MiddlewareStage` (the enum every middleware implementation
  needs, with zero dependencies — exactly what kernel exists to hold).
  `agents/middleware/_contracts.py`'s `Middleware`/`MiddlewareContext` are now
  the only definitions of those names. Same investigation also converted
  `CancellationToken` (`kernel/agent/runtime_context.py`) from a concrete
  class (real `asyncio.Event`, callback list) into a pure Protocol, moving the
  concrete implementation to a new file, `agents/runtime/cancellation.py`.
  Deleted `RunMeta.standalone()` — a classmethod with zero production
  callers that could no longer construct a concrete `CancellationToken` from
  within kernel without violating kernel independence; a test-local
  `_standalone_meta()` helper replaces it in `tests/kernel/test_runtime_contracts.py`.
  Gate: `uv run ruff check .` clean, `uv run lint-imports` 5/5 contracts kept,
  `tests/architecture/test_kernel_invariants.py` 10/10 passed, full suite 441
  passed.
- **Middleware rebuilt as one Protocol/context/pipeline — no different kinds**
  (2026-07-04, final iteration — supersedes both an earlier RunContext-only
  hook and a subsequent three-pipeline `MiddlewareBundle`, per explicit user
  direction: "a middleware is a middleware across the framework, no different
  kinds"; **superseded again on 2026-07-05** — see the "Kernel dead-code
  cleanup" entry above, which deleted the kernel-side `Middleware` Protocol
  and `MiddlewareContextProtocol` entirely once they were found to have zero
  real consumers). `agents/middleware/_contracts.py` defines one concrete
  `Middleware` Protocol and one `MiddlewareContext` dataclass (deleting
  `AgentCallContext`/`ChatContext`/`FunctionContext`) with a `stage` field
  (backed by kernel's `MiddlewareStage` enum — `TURN`/`CHAT`/`TOOL`, the one
  piece of this that does still live in `kernel/agent/middleware.py`) and
  three precisely-typed result slots (`turn_result: AgentRunResult`,
  `chat_result: LLMResponse`, `tool_result: InvocationResult` — kept separate
  rather than one `Any`, since the three result shapes are genuinely
  different classes). A middleware that only cares about one stage declares
  `stages: ClassVar[frozenset[MiddlewareStage]]`;
  `MiddlewarePipeline.execute()` (`agents/middleware/pipeline.py`, its
  duplicate `MiddlewareProtocol` also deleted in favor of one shared
  `Middleware` Protocol) filters to only the middlewares that declared the
  current context's stage before building the call chain — a middleware
  that didn't declare a stage never gets `process()` called for it there.
  `ReActAgent.__init__` (`agents/core/react.py`) takes exactly one
  `middleware: MiddlewarePipeline`; `_handle_message()` builds a
  `MiddlewareContext(stage=TURN, ...)` and sets `c.turn_result` from
  `_react_loop()`'s real `AgentRunResult`. `agents/runtime/context/`'s
  (now a package: `journal.py`/`llm.py`/`tool.py`/`messaging.py`/
  `supervision.py`) `llm()`/`tool()` build `CHAT`/`TOOL`-stage contexts around
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
    `runtime.register`), instead appending a `run.failed` EventLogProtocol entry
    (`status: "version_mismatch"`) and terminally failing the run via the
    new `Scheduler.fail_pending_run()` (a direct pending→failed
    transition for specs that were never leased in this process, so no
    `Lease` exists to hand to `release()`) + `SupervisorProtocol.finish_run()`. This
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
    'running', 'suspended')`); `SchedulerProtocol.enqueue(..., thread_id=...)` raises
    the new `ThreadBusyError` (kernel) on conflict; `routes/chat.py` does a
    cheap `find_run_for_thread()` pre-check for a clean 409 in the common
    case, `Runtime.submit()` is the authoritative enforcement (the rare race
    surfaces as a `run.failed` SSE event instead, since a 409 isn't possible
    once SSE headers are sent). `thread_locks`/`cancel_registry` deleted
    entirely from `ServerDependencies`/`app.py`. Cancel is now
    `routes/cancel.py` resolving the active run via `find_run_for_thread()`
    then calling both `Runtime.cancel()` (fast, same-process best-effort)
    and `SupervisorProtocol.cancel()` (durable, cross-replica — the actual
    guarantee). `AgentStreamSession` dropped its `cancel_event` entirely;
    cancellation from *any* replica is observed the same way completion
    always was — a `run.cancelled` EventLogProtocol entry appearing under tail().
    HITL: new `SchedulerProtocol.find_run_by_wake_signal(name)` lets
    `BridgeRegistry.resolve()` fall back to a durable lookup
    (`ravi_run_queue.wake_signals`) when no local bridge owns a
    `request_id` — the cross-replica case. Memory-seed race:
    `RedisHistoryProvider.try_acquire_seed_lock()` (atomic `SET NX EX`)
    guards the seed inside `CachedHistoryProvider._ensure_seeded()`
    (`capabilities/history/cached_history.py`, formerly
    `agents/factory.py::load_session_memory()`, since removed) — closes the
    double-seed-truncates-older-messages bug at the root (prevents the race
    that caused it, rather than working around the truncation symptom).
  - **Phase 3**: `Thread` gained a `tenant_id` column (additive migration in
    `serving/monolith/database.py`, mirroring the `ravi_run_queue` pattern);
    `get_owned_thread()` now requires matching tenant, not just matching
    user, with the same claim-on-first-access affordance for legacy NULL
    rows. `Lease` (kernel) gained a `tenant` field, threaded from
    `Scheduler`/`InMemoryScheduler.lease()`; `Worker._run_agent()`
    finally populates `RunMeta.tenant_id` from it (previously always `None`
    regardless of what was enqueued — the field existed end-to-end but
    nothing wrote it). `Scheduler.lease()`'s claim query rewritten
    as `ROW_NUMBER() OVER (PARTITION BY tenant ORDER BY priority,
    enqueued_at)` — a tenant flooding the queue can no longer push another
    tenant's run down to a fixed low rank; both always compete for the next
    slot. `Supervision` (kernel) gained `to_dict()`/`from_dict()`;
    `ravi_run_tree.supervision` (JSONB) persists it at spawn time; new
    `SupervisorProtocol.supervision_of(run_id)` lets the Worker rehydrate it into
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
  `Supervisor.cancel()`'s suspended-terminal-mark,
  `Scheduler.lease()`'s deadline-fail) backs a new
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
  stale `SupervisorProtocol` cancellation-cascade docstring describing the
  pre-PR7 wakeup-message design. Added 7 new decision records to
  `decisions.md` (SuspendInterrupt/replay-from-top, path-derived
  determinism, all-Postgres coordination, heartbeat-based cross-process
  cancel) and rewrote `architecture/runtime-stages.md`'s "known gap" section
  — it described exactly the durability hole PR4-PR7 closed. New tests:
  `test_pg_signal_gc_on_finish`, `test_pg_retention_sweep`. Full suite green
  (436 unit + 13 Postgres integration), 5/5 import-linter contracts, 10/10
  kernel invariants — Phase 1 is now fully shipped.
- **Phase 1 PR4-PR7 — durable coordination core, completed** (2026-07-03):
  the heart of the program — `SignalBusProtocol`/`SupervisorProtocol` coordination moved
  fully off in-process memory onto Postgres.
  - **PR4**: `SignalBus` (`infrastructure/runtime/signal_bus.py`)
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
  - **PR6**: `Supervisor` (`infrastructure/runtime/supervisor.py`)
    — `ravi_run_tree` (parent/root/status) + `ravi_spawn_effects` (spawn
    idempotency keyed by the caller's replay-stable effect_id, mirroring
    `InMemorySupervisor`). `finish_run()` is the new Protocol method
    (replaces `record_completion` in both backends): marks the run
    terminal and fires a `child:{run_id}` signal to the parent in one
    call. `Runtime.__init__` takes an injected `supervisor` (added a
    public `Runtime.supervisor` property alongside the existing
    `event_log`/`inbox`/`signal_bus`). `ctx.join()` rewritten off
    `SupervisorProtocol.join()` (asyncio.Event blocking — the one remaining
    kernel-contract-violating wait) onto the same consume-based
    `child:{run_id}` signal `ask()` already used — a miss raises
    `SuspendInterrupt` like every other suspension primitive.
  - **PR7**: cancel cascade via a recursive CTE in
    `Supervisor.cancel()` — seeds with the handle's own run_id
    unconditionally (a top-level `submit()` run has no `ravi_run_tree` row
    at all; only `ctx.spawn()`'d runs do) then walks `parent_run` down.
    Pending/running runs in the subtree get `cancel_requested=true`
    (observed at the next heartbeat, ≤15s); suspended runs have no live
    task to ever heartbeat, so they're terminal-marked directly via
    `finish_run()`. `SchedulerProtocol.heartbeat()` now returns `bool` (kernel
    Protocol change) — `True` means a durable cancel or deadline was
    observed, and the Worker cancels the run's local
    `CancellationToken` in response (this is how a cancel issued by a
    *different* worker process reaches a live Task, since only the
    leasing worker holds it). Deadline enforcement lives in
    `Scheduler.lease()`'s existing poll: a pending/suspended run
    past its `deadline` column terminal-fails directly (nothing will ever
    heartbeat it); `SchedulerProtocol.enqueue()` gained an optional `deadline`
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
    a joining parent (no SupervisorProtocol reference from `Scheduler`) —
    a parent relying on that instead needs its own `ask()`/`join()`
    timeout, which is unaffected and already correct.
- **Phase 1 PR3 — event-log-as-journal** (2026-07-02): `EffectCache`
  (`agents/runtime/effect_cache.py`) folds a run's `effect.result` EventLogProtocol
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
  *parent's own* EventLogProtocol, bypassing `RunContext`'s new cursor — surfaced as
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
