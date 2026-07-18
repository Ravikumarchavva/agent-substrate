# Playbook: Debugging Suspended / Stuck Runs and HITL Cards

Symptoms this covers: "it's not pausing," "the card doesn't do anything when
I click it," "my answer isn't being used," "the run seems to hang forever."

## Step 1 — separate "backend problem" from "frontend rendering problem"

This distinction matters because the fixes are completely different, and it's
easy to misdiagnose one as the other from the UI alone (verified twice this
session: what looked like "not pausing" and "not continuing" was actually a
frontend state-display bug — the backend had resumed correctly within the
same second).

**Check the server terminal first**, filtered:

```bash
uv run start 2>&1 | grep -iE "suspended|resumed|HITL|signal|pending request"
```

or, if already running, check what already printed. You're looking for:

```
run.suspended                                        ← run actually suspended
Bridge: registered signal HITL <id> → run <run_id>   ← card registered for resolution
Resolved signal HITL <id> (run=<run_id>)              ← click delivered the signal
BridgeRegistry: no pending request for id=<id>        ← click FAILED to find it — real bug
```

Also just watch the raw request log — a `POST /chat/respond/{id}` returning
`200 OK` followed by fresh `POST https://api.openai.com/...` calls within a
second or two means **the backend resumed correctly**. If you see that,
stop looking at the backend — the bug is in `substrate-ui`'s rendering of
loading/pending state, not the suspend/resume mechanism.

## Step 2 — inspect the actual persisted state

There is no separate `steps` table anymore — conversation history was
collapsed onto the EventLog (`substrate_event_log`), which is now the single
source of truth for both runtime replay *and* chat-history display (see
`serving/stream/history.py::project_thread()`). Find the thread's run(s):

```bash
docker exec agent-framework-postgres-1 psql -U postgres -d agentdb -t -c "
SELECT id, updated_at FROM threads ORDER BY updated_at DESC LIMIT 5;"

docker exec agent-framework-postgres-1 psql -U postgres -d agentdb -t -c "
SELECT run_id, status, worker_id, expires_at, wake_at, created_at
FROM substrate_run_queue
WHERE thread_id='<THREAD_ID>'
ORDER BY created_at;"
```

Dump the EventLog for a given `run_id` in order — this is the ground truth
for what actually happened, independent of anything the UI renders:

```bash
docker exec agent-framework-postgres-1 psql -U postgres -d agentdb -t -c "
SELECT seq, kind, ts, left(payload::text, 160) AS payload
FROM substrate_event_log
WHERE run_id='<RUN_ID>'
ORDER BY seq;"
```

What to look for:
- A `tool.result` entry for `ask_human` immediately followed (same second or
  two) by fresh `text.delta`/`tool.call` entries in a *later* `run_id` for the
  same thread → **the run resumed correctly** (each suspend/resume cycle gets
  its own `run_id` — check `substrate_run_queue` for all runs on the thread,
  not just the most recent), the bug is elsewhere (frontend, or the LLM's own
  answer-processing logic).
- Check the actual `tool.result` payload for `ask_human` — if the answer
  looks like a template/placeholder rather than real data (e.g.
  `"Dine in • budget • food type • area • vibe"`), the *model* built a bad
  question/options, not a plumbing bug. See
  [`architecture/hitl.md`](../architecture/hitl.md) and the `AskHumanTool`
  description for the guardrail meant to prevent this.
- Card-reconstruction rule still applies unchanged (see
  [`decisions.md`](../decisions.md#card-reconstruction-reads-from-tool_result-never-assistant_messagetool_calls)):
  UI state must be rebuilt from `tool.result` payloads, never from a
  `tool.call` entry's arguments — the turn-flush logic can drop `tool_calls`
  for ask-only turns.

## Step 3 — if the backend genuinely never resumed

Check these in order:
1. **Is the tool marked `suspends = True`?** If not, the per-call timeout
   (`ChainPolicy.call_timeout_s`, default 60s) will have cancelled the
   suspended coroutine before your answer arrived. See
   [`decisions.md`](../decisions.md#tools-that-suspend-must-declare-suspends--true).
2. **Did the monolith process restart** between suspend and your click? This
   is fine for both HITL kinds now — suspended runs are durable (fixed
   2026-07-03, see [`architecture/runtime-stages.md`](../architecture/runtime-stages.md)),
   and tool-approval was migrated onto the same `ctx.sleep_until_signal()`
   path `ask_human` already used (see `ToolInvoker._invoke_inner` in
   `agents/tools/invoker.py` and `SSEApprovalHandler` in
   `serving/monolith/sse/approval.py`). Any worker on any replica can
   resume from `substrate_run_queue`/`substrate_event_log` after a full
   restart, for either kind. If you still see a lost approval, that's a
   real regression — check `substrate_event_log` for an
   `approval.requested` entry with the request_id from the stale card, and
   confirm `substrate_signals` has (or ever had) a matching
   `hitl:{request_id}` row.
3. **Is `run_id` actually reaching the frontend?** Check
   `InputRequestedEvent.run_id` isn't empty — `BridgeRegistry.register_signal_request()`
   only fires `if wire.run_id and run_id`. An empty `run_id` means the click
   can never find the right run to signal.

## Step 4 — if the card renders wrong or doesn't reload

See [`decisions.md`](../decisions.md#card-reconstruction-reads-from-tool_result-never-assistant_messagetool_calls) —
reconstruction must read from `tool_result` (`_card` embedded payload), not
from `assistant_message.tool_calls`. Also check field-name mismatches: the
persisted `ToolUseBlock` shape uses `tool_name`/`call_id`, while some other
code paths use `name`/`id` — normalize both when parsing.
