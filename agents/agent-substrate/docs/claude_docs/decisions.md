# Decisions — Check Here Before Re-litigating

Short ADR-style entries: the decision, why, and what it rules out. Edit in
place when a decision changes; note the date and reason for the change rather
than deleting history.

---

## Suspension uses `SignalBus.signal()`, never `asyncio.Future`

**Decision:** any tool or agent behavior that pauses a run waiting on an
external event (human input, a timer, another agent) uses
`ctx.sleep_until_signal(name)` — never holds an `asyncio.Future` open inside
the running Task.

**Why:** a pending Future keeps the agent's asyncio Task alive — it's not
truly dormant, and per `kernel/runtime/wakeup.py`'s docstring, a properly
suspended run should cost "zero RAM and zero CPU." The Future pattern also
means a 60-second-or-so tool-call timeout will cancel the coroutine mid-wait
if the human takes too long, silently dropping their eventual answer — this
was a real, shipped bug in `ask_human` before the signal-based migration
(see [`roadmap.md`](roadmap.md) "Recently shipped").

**Ruled out:** adding more Future-based HITL mechanisms. The existing tool
approval path (`ToolApprovalHandler`) still uses Futures — that's tracked
debt to migrate ([`roadmap.md`](roadmap.md) P1), not a template to copy from.

**Update (2026-07-03):** the durability gap this caveat used to describe is
closed — `PostgresSignalBus` + `SuspendInterrupt`-based suspend/resume
(Phase 1 PR4-PR5, see [`roadmap.md`](roadmap.md) "Recently shipped") means a
suspended run's `ravi_run_queue.status` row is genuinely `'suspended'` and
survives a process restart. See
[`architecture/runtime-stages.md`](architecture/runtime-stages.md) for the
current state and the one remaining boundary case (durable `deadline`
column has no writer yet).

---

## Tools that suspend must declare `suspends = True`

**Decision:** any `Tool` whose `execute()` calls `ctx.sleep_until_signal()`
(or otherwise blocks on a human/external event) must set `suspends: bool = True`
as a class attribute.

**Why:** `ToolInvoker` (`agents/tools/invoker.py`) wraps every tool call in
`asyncio.wait_for(..., timeout=policy.call_timeout_s)` by default. A human can
take minutes; the invoker checks `getattr(tool, "suspends", False)` and skips
the timeout wrapper entirely for such tools. Forgetting this flag reproduces
the exact "answer silently dropped after 60s" bug described above.

**Ruled out:** raising `call_timeout_s` globally to "fix" this — that would
let a genuinely hung *non-suspending* tool block the run for minutes instead
of failing fast.

---

## Kernel (L0) is frozen — new contracts need zero deps and multi-layer need

**Decision:** `kernel/` only grows when a new contract (a) has zero external
dependencies and (b) is genuinely needed by more than one layer above it.
Otherwise the code goes in `agents/`, `capabilities/`, or `fabric/`.

**Why:** kernel's entire value is that every backend (in-memory today,
Postgres/Redis tomorrow) can implement its Protocols and be swapped freely.
A concrete helper snuck into kernel breaks that swappability guarantee and
creates a backdoor dependency that `lint-imports` can't catch (it only checks
import direction, not "should this Protocol exist at all").

**Enforcement:** `tests/architecture/test_kernel_invariants.py` — LOC ceiling
(6k), file-count ceiling (45), no concrete implementations (only
Protocols/ABCs/dataclasses/enums). These are tripwires, not the actual review
— use judgment on *whether something belongs*, not just whether it fits under
the ceiling.

---

## Card reconstruction reads from `tool_result`, never `assistant_message.tool_calls`

**Decision:** any UI state that must survive a page reload and depends on a
tool call's arguments (like the `ask_human` card's question/options) should
be embedded in the tool's **result** payload, not read back from the
assistant turn's persisted `tool_calls`.

**Why:** verified in production — a 3-question `ask_human` turn persisted all
3 `tool_result` rows correctly, but the assistant_message's `tool_calls` array
came back **empty**. The turn-flush logic in `AgentStreamSession` (which
decides when to persist an assistant turn) can drop tool_calls for ask-only
turns depending on timing. `tool_result` rows, by contrast, are always
persisted per-call and are the more reliable source of truth.

**Applies beyond HITL:** if you're building any other "reconstruct rich UI
state from history" feature, default to reading from tool_result payloads,
and treat `assistant_message.generation.tool_calls` as best-effort /
supplementary, not authoritative.

---

## Task-board anchoring uses `TaskList.created_at`, not a live-only frontend map

**Decision:** the frontend attaches a plan board to the chat turn that created
it by comparing the board's persisted `created_at` timestamp against user
message timestamps — not by an in-memory `Map` populated only during active
streaming.

**Why:** the original implementation (`boardAnchors` state in `page.tsx`) was
cleared on every thread-id change, which happens right after the first
message promotes a new thread. Any board created before that point lost its
anchor and fell back to "attach to the most recent user message" — so simply
sending a second message would visually relocate an earlier turn's board.
This reproduced live, not just on reload.

**Ruled out:** trying to patch the live-anchor map to survive thread
promotion — the underlying problem is that ephemeral client state can't
reliably answer "which turn created this," only the server's timestamp can.

---

## Suspension is `SuspendInterrupt` unwind + replay-from-top, never coroutine pickling

**Decision:** every primitive that suspends a run (`ctx.ask`, `ctx.join`,
`ctx.sleep_until_signal`, `ctx.sleep_until`) follows one shape: attempt a
non-blocking claim (`SignalBus.consume()` or a direct wall-clock check); on a
miss, raise `SuspendInterrupt` — a `BaseException` subclass in
`kernel/core/errors.py`. This unwinds straight out of `agent.run()` to the
Worker, which releases the lease as `SUSPENDED` and lets the asyncio Task end
completely. Resume is a fresh lease: any worker folds a new `EffectCache`
from the EventLog and calls `agent.run()` again from the top; every
already-completed effect and already-consumed signal is a cache/consume hit,
so execution silently fast-forwards back to the same wait point.

**Why:** the alternative — serializing/pickling a paused coroutine's stack to
resume it later — doesn't exist in Python in any general, safe form, and even
language-level continuations wouldn't survive a process crash cleanly. A
`BaseException` (not `Exception`) is deliberate: ordinary `except Exception`
handlers in agent or tool code (guardrails, retry wrappers, `try/except
Exception` around a tool call) must NOT be able to swallow a suspension and
silently turn "go dormant" into "crash" or "keep running as if nothing
happened."

**Ruled out:** giving `SignalBus` a blocking `wait_for_signal()`/`Supervisor`
a blocking `join()` that parks a live coroutine — that was the actual
original implementation and is exactly what made suspended runs
non-durable (see the "signal-based pattern isn't fully durable" caveat
above, now resolved). Both `InMemorySupervisor.join()` and
`PostgresSupervisor.join()` still exist, purely for kernel `Supervisor`
Protocol conformance — nothing in this codebase calls them; `ctx.join()`
consumes a `child:{run_id}` signal exactly like `ctx.ask()` does instead.

---

## Effect/signal identity is derived from `RunContext._alloc_path()`, never from message fields or freshly-computed values

**Decision:** any deterministic id needed inside `agent.run()` — an
`effect_id`, a `correlation_id`, a spawned child's identity — is derived from
`RunContext._alloc_path()` (a hierarchical position: "the Nth journaled call
in this run, nested under the Mth journaled call before it"). Never from a
`Message.id`/`Message.correlation_id` the agent constructs itself, and never
from a value computed fresh at call time (e.g. "the parent log's current
`last_seq`").

**Why:** found the hard way, three times, while building durable
suspend/resume (Phase 1 PR5): agent `run()` code re-executes in full on every
replay attempt. A fresh `Message()` constructed inside `run()` gets a new
`uuid4()`-derived `.id`/`.correlation_id` on every attempt — so anything keyed
off it (a signal's consume-effect_id, an ask's correlation_id) can never be
re-claimed on replay; it looks like a brand-new, never-consumed wait every
time. Likewise, deriving an effect_id from "the parent's current `last_seq`"
is self-referentially unstable: the very act of spawning is what advances
that counter, so replaying the same spawn call computes a *different* id than
the live attempt did. `_alloc_path()` has neither problem — it's a pure
function of "how many times has this run's code, at this exact call site,
reached a journaled call before," which is identical on every replay by
construction (see `RunContext`'s class docstring in `context.py`).

**Concretely fixed by this pattern:** `ctx.ask()`'s correlation_id,
`Supervisor.spawn()`'s effect_id (removed `boot.id` from the hash args
entirely), and a `spawn()`+`ask()` double-delivery collision closed by
`RunHandle.boot_correlation_id` (spawn's boot delivery and a subsequent
`ctx.ask()` to the same handle must never both deliver — see that field's
docstring in `kernel/runtime/supervisor.py`).

---

## Coordination state (signals, supervision, cancel, deadlines) is all-Postgres, one database, transactional with the scheduler

**Decision:** `SignalBus`, `Supervisor`, and the scheduler's wakeup/cancel/
deadline columns (`wake_signals`, `wake_at`, `cancel_requested`, `deadline`)
all live in the same Postgres database as `EventLog`/`Inbox`/`Scheduler`
(`ravi_signals`, `ravi_run_tree`, `ravi_spawn_effects` alongside
`ravi_run_queue`), not in Redis or a separate coordination store.

**Why:** the lost-wakeup race (a signal arriving between a suspending run's
"nothing here yet" check and its `release(SUSPENDED)` landing) and the
cancel-cascade race (a run finishing between a cascading cancel's read and
its write) both need to be closed with actual transactions, not "check twice
and hope." `PostgresScheduler.release(SUSPENDED)` double-checks
`ravi_signals` for an already-arrived signal in the same transaction as the
park; `PostgresSignalBus.signal()` does the INSERT and the matching-run wake
in one transaction. Neither is expressible cleanly across two different
datastores (Postgres for the scheduler, Redis for signals) without a
distributed transaction or an accepted race window. `RedisJournal` was
already removed from the effect-durability path in PR3 for the same class of
reason (a TTL'd store silently expiring mid-suspension); Redis's role in this
codebase is now cache/rate-limiting only, never correctness.

**Ruled out:** a Redis-backed `SignalBus` (the original Stage 1 direction
documented in `kernel/runtime/wakeup.py`'s docstring before this decision) —
superseded once the race-closure requirement above became clear during PR4
design. If that docstring still says Redis, it's stale; Postgres is correct.

---

## Durable cross-process cancellation rides the existing heartbeat, not a new push channel

**Decision:** `Supervisor.cancel()` cascading to a run currently owned by a
*different* worker process reaches that process via `Scheduler.heartbeat()`
returning `bool` (kernel Protocol change, Phase 1 PR7) — the Worker's
periodic heartbeat call now also reads back `cancel_requested`/`deadline`
and cancels the run's local `CancellationToken` when either fires. No new
push mechanism (a second signal, a pub/sub cancel channel) was added.

**Why:** a cancelling process has no reference to another process's live
asyncio Task — the only channel already crossing that boundary on a fixed,
bounded cadence is the heartbeat every running lease already sends
(`_HEARTBEAT_INTERVAL = 15s` in `worker.py`). Reusing it means cancellation
latency is bounded (≤15s) and requires no new infrastructure. Suspended runs
are the one case heartbeat can't reach (no live task polling anything) —
those are terminal-marked directly by `Supervisor.cancel()` instead, since
nothing will ever heartbeat them again.

**Ruled out:** a lower-latency push-based cancel (e.g. `pg_notify` straight
to a listening Worker). Not implemented because nothing in the program's
verification criteria demanded sub-15s cancel latency, and adding a second
live-Task-reachable channel alongside heartbeat would be complexity without
a stated requirement driving it — revisit if a real latency requirement
shows up.

---

## Single-flight and cancel are enforced by the Scheduler/Supervisor, never a per-process lock or Event

**Decision:** "only one active run per thread" and "stop this run" are both
enforced entirely through durable state (`ravi_run_queue.thread_id` +
unique partial index; `Supervisor.cancel()`'s `cancel_requested`/terminal-mark)
— never through an `asyncio.Lock`/`asyncio.Event` owned by the HTTP request
handler (`ServerDependencies.thread_locks`/`cancel_registry`, both deleted
in Phase 2, 2026-07-03).

**Why:** a per-process primitive is invisible to every OTHER replica. Two
uvicorn workers (or two pods) each hold their own empty `thread_locks` dict
— a second `POST /chat` for a thread already streaming on a *different*
replica sails right through the check. Same failure mode for cancel: a
`POST /cancel` landing on a different replica than the one running the
stream found nothing in its local `cancel_registry` and silently did
nothing. Neither of these was a live bug in a single-replica deployment,
which is exactly why it went unnoticed — it only manifests once you actually
scale out, which is the whole point of Phase 2.

**How resolution propagates:** the SSE-serving replica never needs to be
told about a cross-replica cancel directly — it just keeps doing what it
already does, tailing the run's EventLog. A durable cancel eventually
appends a `run.cancelled` entry (via the owning worker's heartbeat noticing
`cancel_requested`, or immediately if suspended), and `AgentStreamSession`'s
tail loop treats that exactly like any other termination. No new "who do I
need to notify" logic was needed on the reading side at all — see
`AgentStreamSession._check_disconnect`'s docstring.

**Ruled out:** adding a cross-replica pub/sub channel (Redis, `pg_notify`)
purely to propagate "someone cancelled this" faster. The EventLog's own
LISTEN/NOTIFY-backed tail already delivers `run.cancelled` to every replica
tailing that run — a second notification channel would be solving a
problem that doesn't exist.

---

## Deferring a fix is a recorded decision, not a silent gap

**Decision:** when a sub-item of a larger remediation phase turns out to be
a substantially bigger feature than the surrounding fix (e.g. migrating
tool-approval off Futures onto signals, or wiring `agent_runtime` to run an
`AskHumanTool` against `human_gate`), it gets scoped out explicitly and
recorded in `roadmap.md`'s "Explicitly deferred" section — with the
reasoning for why it's out of scope — rather than either (a) rushed through
shallowly to claim the phase "done," or (b) silently dropped with no trace.

**Why:** "Phase 2/3/4 done" as a checkbox is meaningless if it papers over
a rushed, undertested piece bolted onto otherwise-solid work — the next
person (or the next session) needs to know precisely which claims are
backed by tests and which are explicitly punted, not have to re-derive that
by reading a diff. A recorded deferral with reasoning also does the
scoping work once, so it doesn't need re-litigating from scratch next time
someone considers picking it up.

**How to apply:** before marking any multi-part item "done," ask whether
every sub-bullet actually shipped with test coverage. If not, split it: the
parts that did ship are "done," the parts that didn't get their own
"deferred" entry with the concrete reason (bigger scope, negligible real
risk, needs a design decision first, etc.) — not folded into a vague
"mostly done."

---

## A version-mismatched persisted run spec fails cleanly, never resumes silently

**Decision:** when `resume_pending_runs()` (`infrastructure/serving_factory.py`)
finds a cold-resume spec whose `agent_version` doesn't match the running
`substrate.__version__`, it refuses to rebuild/register the agent. The run
is terminally failed instead (`run.failed` EventLog entry with
`status: "version_mismatch"`, `PostgresScheduler.fail_pending_run()`,
`Supervisor.finish_run(FAILED)`).

**Why:** replay-from-top (Phase 1's suspend/resume design) re-executes the
agent's actual code path and only skips effects it finds in the journal.
If the code has changed since the spec was persisted — a tool renamed or
removed, prompt logic altered, control flow restructured — the replayed
effect path can silently diverge from what the journal expects, corrupting
the run in a way that's hard to detect (unlike a hard crash, which is
loud). Resuming-with-a-warning was considered and rejected: it has the same
silent-divergence risk, just with a log line nobody's watching in the
failure path.

**Ruled out:** silently resuming version-mismatched specs; resuming with
only a warning log. Both leave the divergence risk in place.

**How to apply:** if a future change needs "resume anyway, best-effort" for
some specific version delta (e.g. a genuinely compatible minor bump), that
needs its own compatibility-range design (e.g. semver-range matching, not
exact-equality) — don't relax the exact-match check as a quick fix.

---

## Middleware is one `Middleware` Protocol, one `MiddlewareContext`, one `MiddlewarePipeline` — never split by what it wraps

**Decision (final, 2026-07-04 — supersedes both prior iterations below):**
there is exactly one middleware concept in this framework. `kernel/agent/middleware.py`
defines one `Middleware` Protocol (`process(context, call_next)`) and a
`MiddlewareStage` enum (`TURN`/`CHAT`/`TOOL`). `agents/middleware/_contracts.py`
defines one concrete `MiddlewareContext` dataclass with a `stage` field and
every stage's fields declared (unused ones are `None`) plus three
precisely-typed result fields (`turn_result: AgentRunResult`,
`chat_result: LLMResponse`, `tool_result: InvocationResult` — typed
separately rather than one `Any`, since the three result shapes are
genuinely different classes middleware reads real members off of).
`ReActAgent.__init__` takes exactly one `middleware: MiddlewarePipeline`. A
middleware that only cares about one stage declares that via a class-level
`stages: ClassVar[frozenset[MiddlewareStage]]`; `MiddlewarePipeline.execute()`
filters to only the middlewares whose declared `stages` include the current
context's `stage` before building the call chain — a middleware that didn't
declare a stage never gets `process()` called for it, not even as a no-op
pass-through.

This still dispatches at the same three real call sites as before
(`agents/core/react.py`'s `_handle_message()` builds a `stage=TURN` context;
`agents/runtime/context.py`'s `RunContext.llm()`/`.tool()` build `CHAT`/`TOOL`
contexts) — what changed is that all three now share the *same* pipeline
object and the *same* context class, with `stage` as data rather than type
or attachment-point distinguishing them.

**Why:** the user's explicit read on the two prior iterations — three
separate context types (`AgentCallContext`/`ChatContext`/`FunctionContext`)
bundled via a `MiddlewareBundle` with one `MiddlewarePipeline` per field —
was "we completely messed up middleware... a middleware is a middleware
across the framework, no different kinds." That design also left a
pre-existing duplication unfixed: `kernel/agent/middleware.py` and
`agents/middleware/pipeline.py` each independently declared their own
structurally-identical `Middleware`/`MiddlewareProtocol`, and the kernel
separately aliased `AgentMiddleware`/`ChatMiddleware`/`FunctionMiddleware` as
three "kinds" of the same generic protocol — exactly the proliferation being
rejected. Collapsing to one Protocol/context/pipeline, with `stages`-based
filtering as the only per-middleware customization, resolves both the
literal "no different kinds" ask and the kernel/agents duplication.

**Ruled out:**
- A single `agent.middleware` (kernel `RunContext`-based) hook for
  everything (the *first* iteration, pre-`MiddlewareBundle`). Works for
  middleware that only needs `run_id`/`tenant_id` (tracing), but
  `RunContext` has no `.messages`/`.arguments`/result shape — structurally
  incapable of content filtering, PII detection, token limits, or caching.
- Three separate context types bundled into three separate pipelines (the
  *second* iteration, `MiddlewareBundle`). Fixed the guardrail-wireability
  problem but reintroduced "kinds" at the type and attachment-point level —
  judged wrong by the user despite working correctly.
- Manual self-filtering (`if context.stage != X: return await call_next()`
  at the top of every `process()`) instead of declarative `stages`. Confirmed
  with the user directly: declarative filtering is more robust (can't forget
  the check; a TOOL-only middleware simply never sees a TURN/CHAT context to
  misinterpret) at the cost of one more attribute per middleware class.

**How to apply:** every new middleware — guardrail or infra — is a plain
class with `process(self, context: MiddlewareContext, call_next)` and a
`stages` attribute naming which `MiddlewareStage`(s) it needs. Wire it into
`create_assistant_agent(..., middleware=[...])` (`agents/factory.py`, one
list, order = outermost-first) or directly into `MiddlewarePipeline([...])`
passed to `ReActAgent(middleware=...)`. Add an end-to-end test in
`tests/agents/test_middleware_wiring.py` (through a real `ReActAgent` +
`Runtime`, not just a hand-built context) — a unit test that calls
`.process()` directly with a hand-built context proves the guardrail's logic
but not that it's actually reachable at its real call site.
