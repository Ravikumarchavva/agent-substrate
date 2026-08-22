# Decisions — Check Here Before Re-litigating

## Text-input safety model: Prompt Guard 2 86M, not Opir-edge-multilang

**Decision:** the live-chat-turn jailbreak/prompt-attack classifier is
`meta-llama/Llama-Prompt-Guard-2-86M` (community ONNX export, INT8, via
`onnxruntime` + `tokenizers` — no torch/transformers), not
`knowledgator/opir-edge-multilang-v1.0`, despite Opir winning on nearly
every static axis: Apache 2.0 (vs. Prompt Guard's Llama Community license,
a real obligation — acceptable-use policy + attribution + 700M-MAU clause),
23 languages (vs. 8), 1024-token context (vs. 512, halving document
chunking), and consolidating jailbreak + harmful-content detection into one
model instead of two.

**Why:** real measurement, not the model card. The ONNX export itself
worked and was numerically correct (verified: max abs probability delta
1.27e-07 vs. the original PyTorch model, across benign/jailbreak/Hindi/
long-input/empty-string fixtures) — but at **685-851ms per 512-token scan**,
roughly 50x the card's own claimed 15.6ms p50, and far over the ~250ms
hot-path budget. Root cause not fully chased down (plausibly the exported
graph lacks a fused kernel for ModernBERT's alternating full/sliding-window
attention), because two independent INT8 quantization attempts (default
dynamic, and `MatMul`-only + per-channel) both broke the model outright
(max abs delta 0.996 and 0.729 respectively against the same 1e-3 gate) —
INT8 wasn't a viable path to close the latency gap. Prompt Guard's own
community ONNX/INT8 export, measured the same way on the same hardware,
runs in 20-35ms for short chat-length text.

**Ruled out:** shipping Opir at fp32 anyway (572MB, correct, but 700-850ms
would put a visible stall on every flagged turn) or accepting a
"documents-only, chat-text-only-gets-Prompt-Guard" split just to use Opir
somewhere — the whole point of the Opir bet was consolidating two models
into one; a split defeats that and Prompt Guard already has to be present
for chat text regardless.

**Consequence accepted:** two text models instead of one — Prompt Guard for
jailbreak/prompt-attack, `gravitee-io/bert-mini-toxicity` (11.2M params, 14
languages claimed, OpenRAIL++ — another restricted license, acknowledged)
for content-safety on the document-upload path only, not live chat text.
Both license obligations (Llama Community + OpenRAIL++) are therefore live,
not hypothetical.

**Revisit when:** someone has time to properly fix Opir's ONNX export path
(likely: `onnxruntime-transformers`-specific conversion tooling instead of
raw `torch.onnx.export`, or static INT8 with real calibration data instead
of dynamic) — if that closes the latency gap without breaking correctness,
it's still the better model on every other axis and would let both
`bert-mini-toxicity` and its OpenRAIL++ license go away too.

---


Short ADR-style entries: the decision, why, and what it rules out. Edit in
place when a decision changes; note the date and reason for the change rather
than deleting history.

---

## Suspension uses `SignalBusProtocol.signal()`, never `asyncio.Future`

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
closed — `SignalBus` + `SuspendInterrupt`-based suspend/resume
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
non-blocking claim (`SignalBusProtocol.consume()` or a direct wall-clock check); on a
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

**Ruled out:** giving `SignalBusProtocol` a blocking `wait_for_signal()`/`SupervisorProtocol`
a blocking `join()` that parks a live coroutine — that was the actual
original implementation and is exactly what made suspended runs
non-durable (see the "signal-based pattern isn't fully durable" caveat
above, now resolved). Both `InMemorySupervisor.join()` and
`Supervisor.join()` still exist, purely for kernel `SupervisorProtocol`
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
`SupervisorProtocol.spawn()`'s effect_id (removed `boot.id` from the hash args
entirely), and a `spawn()`+`ask()` double-delivery collision closed by
`RunHandle.boot_correlation_id` (spawn's boot delivery and a subsequent
`ctx.ask()` to the same handle must never both deliver — see that field's
docstring in `kernel/runtime/supervisor.py`).

---

## Coordination state (signals, supervision, cancel, deadlines) is all-Postgres, one database, transactional with the scheduler

**Decision:** `SignalBusProtocol`, `SupervisorProtocol`, and the scheduler's wakeup/cancel/
deadline columns (`wake_signals`, `wake_at`, `cancel_requested`, `deadline`)
all live in the same Postgres database as `EventLogProtocol`/`InboxProtocol`/`SchedulerProtocol`
(`ravi_signals`, `ravi_run_tree`, `ravi_spawn_effects` alongside
`ravi_run_queue`), not in Redis or a separate coordination store.

**Why:** the lost-wakeup race (a signal arriving between a suspending run's
"nothing here yet" check and its `release(SUSPENDED)` landing) and the
cancel-cascade race (a run finishing between a cascading cancel's read and
its write) both need to be closed with actual transactions, not "check twice
and hope." `Scheduler.release(SUSPENDED)` double-checks
`ravi_signals` for an already-arrived signal in the same transaction as the
park; `SignalBus.signal()` does the INSERT and the matching-run wake
in one transaction. Neither is expressible cleanly across two different
datastores (Postgres for the scheduler, Redis for signals) without a
distributed transaction or an accepted race window. `RedisJournal` was
already removed from the effect-durability path in PR3 for the same class of
reason (a TTL'd store silently expiring mid-suspension); Redis's role in this
codebase is now cache/rate-limiting only, never correctness.

**Ruled out:** a Redis-backed `SignalBusProtocol` (the original Stage 1 direction
documented in `kernel/runtime/wakeup.py`'s docstring before this decision) —
superseded once the race-closure requirement above became clear during PR4
design. If that docstring still says Redis, it's stale; Postgres is correct.

---

## Durable cross-process cancellation rides the existing heartbeat, not a new push channel

**Decision:** `SupervisorProtocol.cancel()` cascading to a run currently owned by a
*different* worker process reaches that process via `SchedulerProtocol.heartbeat()`
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
those are terminal-marked directly by `SupervisorProtocol.cancel()` instead, since
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
unique partial index; `SupervisorProtocol.cancel()`'s `cancel_requested`/terminal-mark)
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
purely to propagate "someone cancelled this" faster. The EventLogProtocol's own
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
is terminally failed instead (`run.failed` EventLogProtocol entry with
`status: "version_mismatch"`, `Scheduler.fail_pending_run()`,
`SupervisorProtocol.finish_run(FAILED)`).

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

**Decision (final, 2026-07-04 — supersedes both prior iterations below; the
kernel-location detail was itself corrected 2026-07-05, see the note at the
end of this entry):** there is exactly one middleware concept in this
framework. `agents/middleware/_contracts.py` defines one `Middleware`
Protocol (`process(context, call_next)`) and one concrete `MiddlewareContext`
dataclass; `kernel/agent/middleware.py` holds only the `MiddlewareStage` enum
(`TURN`/`CHAT`/`TOOL`) the `stage` field is typed against. `MiddlewareContext`
has a `stage` field and
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
`agents/runtime/context/`'s (now a package — `journal.py`/`llm.py`/`tool.py`/
`messaging.py`/`supervision.py`) `RunContext.llm()`/`.tool()` build
`CHAT`/`TOOL` contexts) — what changed is that all three now share the *same*
pipeline object and the *same* context class, with `stage` as data rather
than type or attachment-point distinguishing them.

!!! note "2026-07-05 correction: kernel never actually kept a `Middleware` Protocol copy for long"
    The paragraph above originally said `kernel/agent/middleware.py` defines
    the `Middleware` Protocol and a `MiddlewareContextProtocol`. That was
    true only briefly: investigating a direct question about why
    Protocols were being defined in `agents/` at all turned up that this
    kernel-side pair had **zero real consumers** — every actual middleware
    needs the concrete `MiddlewareContext`'s stage-specific fields, so
    nothing outside kernel's own re-export ever imported the kernel copy.
    Deleted both; kernel now holds only `MiddlewareStage`. The decision
    below (one Protocol, one context, one pipeline, `stages`-based filtering)
    is otherwise unchanged — only *where* the Protocol/context class
    physically live was corrected.

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

---

## A dependency audit must check runtime string-based loading, not just `import` statements

**Decision:** before removing a package from `[project.dependencies]` as
"unused," grep for more than direct Python imports — also check config/DSN
strings, plugin-name registries, and anything else that names a package by
string rather than importing it, since those never show up in an
`import`/`from` grep.

**Why:** during the v1 remediation dependency-hygiene pass (2026-07-05),
`psycopg[binary]` was deleted from base dependencies because `grep -rn
"import psycopg"` returned zero hits in `src/`. It was still load-bearing:
`serving/monolith/database.py::init_db()` calls `create_async_engine(settings
.DATABASE_URL)`, and `DATABASE_URL` is a `postgresql+psycopg://` DSN —
SQLAlchemy resolves and imports the `psycopg` driver *by the DSN scheme
string* at connection time, not via a static import anywhere in this
codebase's own source. The removal shipped, and the only reason it was
caught before landing was `tests/serving/test_scheduled.py` happening to
spin up the real monolith lifespan against an actual Postgres connection —
a test relying on incidental integration coverage, not a targeted check.

**Ruled out:** trusting `grep -rn "import X"` / an IDE's "unused import"
pass as sufficient evidence a dependency is dead. It is necessary but not
sufficient — it only proves the package isn't imported *by name in Python
source*, not that nothing in the system names it another way (a connection
string's scheme, a plugin registry's string key, an entry-point name, a
subprocess command).

**How to apply:** when auditing dependencies for removal, in addition to the
import grep: (1) grep the same package/driver name across `.env`/config
defaults and any `Settings`/`Config` class field values (DSN schemes are the
sharpest case — `postgresql+psycopg`, `redis+ssl`, etc.); (2) actually run
the test suite against a real backend (not just mocks) after removing a
package, before considering the removal verified — a green `pytest` run
with only in-memory/mocked backends will not catch this class of bug; (3) if
a removal is in the same pass as several others, don't assume catching one
mistake clears the rest — re-run the full check against each remaining
removal candidate, don't stop at the first one that turns out fine.

---

## Extraction-service embedding/reranking: Qwen3-VL pair via llama-server sidecars, not SigLIP+MiniLM in-process

**Decision:** the extraction service's embedding and reranking models are
`Qwen3-VL-Embedding-2B` and `Qwen3-VL-Reranker-2B` (both Q4_K_M GGUF, Apache
2.0), served as two separate `llama-server` HTTP sidecars
(`deployment/docker/llama-server.Dockerfile`, `docker-compose.yml`'s
`llama-embed`/`llama-rerank` services) — not loaded in-process via
`sentence-transformers`/`CrossEncoder` the way SigLIP + `ms-marco-MiniLM`
were before this. `EmbeddingReranker`
(`runtimes/embedding_reranker/service/embedding.py`) is a thin HTTP client
to these two services — split into its own `runtimes/embedding_reranker`
service (see the "runtimes/ grouping" entry below) since it shares no code
or state with document-intelligence's OCR/layout pipeline.

**Why:** the deployment target has no GPU. Real candidates were narrowed and
verified by actually running them, not by reading spec sheets:
- `jina-clip-v2` / `jina-embeddings-v5-omni-nano` (dense+multimodal,
  otherwise attractive) — ruled out: CC BY-NC-4.0, non-commercial only;
  self-hosting inside a commercial SaaS product is exactly the use that
  license excludes.
- `UEmbed-2B` (Alibaba) — MLX-quantized community builds are Apple-Silicon
  only; the base model needs a GPU or heavy full-precision CPU inference;
  too new (days old at evaluation time) for a mature non-MLX quantization.
- `Qwen3-VL-Embedding-2B` / `Qwen3-VL-Reranker-2B` — real Qwen/Alibaba team
  pair (same paper), Apache 2.0, genuinely multimodal (text/image/video in
  one space), published benchmarks (MMEB-v2 73.2/75.1). Verified by
  building `llama-server` from source and running both for real: correct
  2048-dim embeddings (cosine 0.82 similar-pair / 0.13 dissimilar-pair),
  correct reranking (0.50 relevant / 0.22 irrelevant), ~2.0-2.1GB RSS each.

The in-process Python path was tried first and doesn't work for these two
models: `llama-cpp-python`'s generic embedding call does not expose correct
pooling for either model (first attempt produced inconsistent embedding
dimensions per call — silently wrong, not an error) — only passing
`pooling_type` explicitly fixed the embedder, and the reranker only works at
all via `llama-server`'s dedicated `--reranking` mode + `/rerank` endpoint,
not via any generic embedding call with `pooling_type=RANK`, which produced
garbage (denormalized/uninitialized-looking floats) even after specifying
the pooling type correctly for the embedder case.

**Ruled out:** trusting a model's file size or download page as sufficient
evidence it works — the first GGUF conversion tried (`TitleOS/...` for the
embedder) loaded without error and *looked* fine until dimensions were
actually compared across calls. A clean load + no exception is not proof of
correct output; only checking the actual output shape and running a
real cosine-similarity/relevance sanity check caught it.

**How to apply:** don't raise `llama-embed`/`llama-rerank`'s `--parallel`
above the conservative launch defaults (2 and 1 respectively) without
benchmarking real RSS and latency first — this session's own default,
unset `--ctx-size` run pushed the reranker to ~9.5GB before being caught.
See the RAG pipeline redesign plan for the full pipeline this pair feeds
into (hybrid retrieval, reranking, page-adjacency context).

---

## `runtimes/` top-level grouping; `doc_handler` renamed `document_intelligence`; `embedding_reranker` split out

**Decision:** `src/substrate/runtimes/` is a new orthogonal top-level
package (same tier as `serving/`/`integrations/`/`infrastructure/`, outside
the L0-L3 import-linter layer stack) for independently-deployable,
heavy-dependency, HTTP-only-consumed first-party services. The old
`doc_handler` package moved there and was renamed `document_intelligence`
(a deliberately more professional name — `doc_handler` was informal,
never a considered choice). Its embedding/reranking proxy
(`service/embedding.py`'s `EmbeddingReranker`) was split out into a
sibling `runtimes/embedding_reranker/` package with its own client, config,
FastAPI service, Dockerfile, compose service, and k8s manifest.

**Placement rule for `runtimes/`:** a package belongs there when **both**
(a) it has a heavy/optional dependency footprint the main API process must
never import, and (b) callers only ever reach it via a thin,
always-importable HTTP client — never direct Python import of its service
internals. Contrast with `capabilities/tools/code_interpreter/.../
agent-sandbox/` (a k8s pod template tied 1:1 to one tool, not a
general-purpose HTTP service other parts of the framework call) and
`llama-embed`/`llama-rerank` (no `src/substrate` package at all — pure
`llama-server` binaries, thin client lives inside whichever `runtimes/`
package consumes them).

**Why the split:** `document_intelligence` (PaddleOCR/PPStructureV3 layout
+ OCR) and `embedding_reranker` (a thin httpx proxy to the llama-embed/
llama-rerank sidecars) shared zero code or state — they were co-located
inside one FastAPI app purely by convenience. Splitting lets one person own
document extraction and another own embedding/reranking infra without
touching each other's files, and gives `embedding_reranker` its own,
much lighter deployment footprint (no local model, no `paddlepaddle`
dependency) instead of inheriting `document_intelligence`'s heavy image.

**Why now, not just documented for later:** no prior decision recorded why
`doc_handler` was a bare top-level package (confirmed absent from this
file and `roadmap.md` via full-file grep) — this was genuinely undecided
precedent, not something worth preserving out of caution.

**Full rename map:** Python package
`doc_handler` → `runtimes/document_intelligence`; pyproject extras
`doc-handler`/`doc-handler-gpu` → `document-intelligence`/
`document-intelligence-gpu`; env prefix `DOC_HANDLER_` →
`DOCUMENT_INTELLIGENCE_` (embedding_reranker gets its own new
`EMBEDDING_RERANKER_` prefix); Dockerfiles, compose services/profiles, k8s
manifest, CI workflow, and Makefile targets renamed to match
(`document-intelligence`/`document-intelligence-gpu`); new
`embedding-reranker` Dockerfile/compose service (port 8023)/k8s manifest/
Makefile target added.

**Also added in the same pass:** a `DocumentExtractor` kernel Protocol
(`kernel/storage/document.py`, mirroring `VectorStore`/`GraphStore`/
`HistoryProvider`'s shape) with `ExtractionPipeline`
(`PPStructureV3`-backed, `runtimes/document_intelligence`) as one real
implementation and a new `capabilities/knowledge/loaders/
xycut_extractor.py` (pdfplumber word-level bboxes + a fixed
`recursive_xy_cut` — upstream paddlex's version crashes on an empty
x-interval chunk at word-level granularity; the fix is a one-line guard) as
a second, lightweight, no-OCR-needed implementation for digital PDFs. Also
wired `ExtractionClient` into `PDFLoader` itself (previously only
`LocalRagBackend` called it directly, not `PDFLoader` when constructed
bare).
