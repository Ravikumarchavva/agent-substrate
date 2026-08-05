# Human-in-the-Loop — Three Mechanisms, Converging

There are **three HITL implementations** in this codebase. Two — `ask_human`
and tool approval, both monolith — are now "dead suspend" (zero compute
while waiting, durable across a process restart) on the identical
`ctx.sleep_until_signal()` path. The third (`human_gate`, microservices) has
converged on the same wire signal but isn't fully wired to a live tool yet —
see [`roadmap.md`](../roadmap.md).

## 1. `ask_human` — signal-based, monolith (the target pattern)

**Where:** `capabilities/tools/human_input.py` (`AskHumanTool`),
`serving/monolith/sse/bridge.py` (`WebHITLBridge`), `console/hitl.py` (console).

**How it works:**
1. `AskHumanTool.execute()` checks `getattr(self.handler, "suspends_via_signal", False)`.
   If true: logs `input.requested` to the EventLog, then calls
   `await ctx.sleep_until_signal(f"hitl:{request_id}")` — this **suspends the
   asyncio coroutine** (see [`runtime-stages.md`](runtime-stages.md) for what
   "suspend" actually means and its durability gap).
2. The frontend renders a card the moment `input.requested` streams through
   the normal event-log tail (`STREAMING_KINDS` in `serving/protocol/from_log.py`).
3. User clicks an option (or Skip, or types free text) → frontend POSTs to
   `/chat/respond/{request_id}` → `WebHITLBridge.resolve()` fires
   `SignalBus.signal(run_id, f"hitl:{request_id}", payload)`.
4. The suspended coroutine wakes, `_shape_result()` maps the signal payload
   (`{action: "answered"|"skipped"|"cancelled"}`) to a `ToolExecutionResult` —
   every branch returns a valid result so no `tool_use` is ever left without a
   matching `tool_result`.

**Critical detail — the timeout exemption:** `AskHumanTool.suspends = True`
tells `ToolInvoker` (`agents/tools/invoker.py`) to skip the normal
`asyncio.wait_for(..., timeout=call_timeout_s)` wrapper. Without this, a human
taking longer than the per-call timeout (60s default) gets their coroutine
cancelled mid-wait and the answer is silently dropped. **Any new tool that
suspends the run must set `suspends = True`** or it will hit this exact bug.

**Card reconstruction on reload:** the assistant turn's `tool_calls` are *not*
a reliable source for rebuilding answered cards — turn-flush timing can drop
an ask-only turn's tool_calls entirely (verified in production: a 3-question
turn persisted all 3 `tool_result` rows but an *empty* `tool_calls` array on
the assistant_message). The fix: `_shape_result()` embeds the original
question/options under a `_card` key directly in the `tool_result` JSON, which
*is* reliably persisted every time. `substrate-ui/src/lib/api/messages.ts`
reconstructs cards from `tool_result` rows using `_card`, never from
`assistant_message.tool_calls`.

**Skip is always offered** — the frontend always renders a Skip button
regardless of what the agent requested; the agent doesn't get to suppress it.

## 2. Tool approval — signal-based, monolith (migrated)

**Where:** `agents/tools/invoker.py` (`ToolInvoker._invoke_inner`'s approval
branch), `serving/monolith/sse/approval.py` (`SSEApprovalHandler`).

Mirrors `ask_human` exactly: `ToolInvoker` checks
`getattr(self._approval, "suspends_via_signal", False)` — `SSEApprovalHandler`
sets this marker whenever its `WebHITLBridge` has a real `signal_bus`. When
set, `ToolInvoker` logs `approval.requested` (`ctx.log_once`, replay-stable
`request_id` via `ctx.uuid()`) and suspends via
`ctx.sleep_until_signal(f"hitl:{request_id}")` — the identical signal
namespace `ask_human` uses, so `WebHITLBridge.register_signal_request()`/
`resolve()`/`cancel_signal_requests()` needed zero new methods. A Future-based
`request()` fallback still exists on `SSEApprovalHandler` for a handler
constructed without a `signal_bus` (tests, or a deliberately non-durable
setup) — the normal path never calls it.

All three risk-gated tools that used to carry a `# TODO: L4-hitl` marker
(`capabilities/tools/web/surfer.py`, `capabilities/tools/code_interpreter/
tool.py` — since consolidated into a single `CodeInterpreterTool` behind a
pluggable `SandboxRuntime`) needed no code changes themselves — the durability
fix lives entirely in the invoker/bridge layer, so any tool declaring
`risk = "critical"`/`"sensitive"` gets the durable gate automatically. Markers
removed.

## 3. `human_gate` microservice — Postgres + Redis pub/sub, now signal-converged

**Where:** `serving/services/human_gate/` (`HITLRequest` ORM model).

**Updated 2026-07-05:** the divergence described here has narrowed. As of
Phase 4 (2026-07-03, see `roadmap.md`), `human_gate`'s `resolve_request()`/
`cancel_pending_for_thread()` accept a `signal_bus` and fire the *same*
`hitl:{request_id}` signal `AskHumanTool`'s signal-suspend path
(mechanism #1 above) waits on via `ctx.sleep_until_signal` — alongside the
existing Redis pub/sub publish. `POST /hitl/request` also now exists (it
didn't before — `create_request()` was previously unreachable). So the wire
protocol has converged onto the same signal mechanism; what's still missing
is wiring `agent_runtime` to actually construct an `AskHumanTool` with a
`suspends_via_signal=True` handler against it — `app.state.tools` there is
still a single static list built once at lifespan startup, with no per-run
tool customization (explicitly deferred, see `roadmap.md`'s "Explicitly
deferred" section). Also unresolved: `human_gate`'s own response body shape
(`HITLResponseBody`: `approved`/`value`/`responded_by`, in
`serving/services/human_gate/routes.py`) and `AskHumanTool._shape_result()`'s
expected payload shape (`action`/`value`) were built independently and don't
fully align — `resolve_request()`'s signal payload does a best-effort mapping
that hasn't been validated against a real `AskHumanTool` call. If you're
working in microservices mode: the signal wire-format now matches the
monolith's, but the tool-side wiring to actually use it doesn't exist yet —
don't assume a working end-to-end HITL flow through `agent_runtime`.

## Rule of thumb for new HITL-shaped tools

- **Monolith, new tool that pauses the run:** follow the `ask_human` pattern —
  `suspends = True`, log `input.requested`-equivalent, `ctx.sleep_until_signal()`.
  Don't add a fourth Future-based mechanism.
- **Anything that needs approval before executing (not asking a question):**
  declare the tool's real `risk` tier — `ToolInvoker` gates it durably via
  the signal path automatically (#2 above). No per-tool wiring needed.
- **Microservices:** use `human_gate`, not the monolith bridge — they're not
  interchangeable.
