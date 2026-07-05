# Human-in-the-Loop — Three Mechanisms, One Intended Direction

There are currently **three different HITL implementations** in this codebase.
Only one (`ask_human`, monolith) is "dead suspend" (zero compute while
waiting). The other two are historical and should eventually converge on the
same signal-based pattern — see [`roadmap.md`](../roadmap.md) P1/P2.

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

## 2. Tool approval — Future-based, monolith (not yet migrated)

**Where:** `WebHITLBridge._handle_approval()`, `ToolApprovalHandler`.

Still uses `asyncio.Future` + `_request_and_wait()` — the agent's Task stays
alive (not truly suspended) while blocked on the Future. This is the same
flaw `ask_human` had before the signal-based migration. Three tools are
explicitly marked as needing this migration via `# TODO: L4-hitl` comments:
`capabilities/tools/web/surfer.py`, `capabilities/tools/code_interpreter/tool.py`,
`capabilities/tools/code_interpreter/code_interpreter/k8s_tool.py` (all
`risk = "critical"` or `"sensitive"`).

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
  currently still Future-based (#2 above) until that migration happens.
- **Microservices:** use `human_gate`, not the monolith bridge — they're not
  interchangeable.
