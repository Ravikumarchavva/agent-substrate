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

Find the thread:

```bash
docker exec agent-framework-postgres-1 psql -U postgres -d agentdb -t -c "
SELECT id, updated_at FROM threads ORDER BY updated_at DESC LIMIT 5;"
```

Dump its steps in order — this is the ground truth for what actually
happened, independent of anything the UI renders:

```bash
docker exec agent-framework-postgres-1 psql -U postgres -d agentdb -t -c "
SELECT type, name, is_error, created_at, left(coalesce(output,input,''),120) AS content
FROM steps
WHERE thread_id='<THREAD_ID>'
ORDER BY created_at;"
```

What to look for:
- A `tool_result` row for `ask_human` immediately followed (same second or
  two) by new `assistant_message` rows → **the run resumed correctly**, the
  bug is elsewhere (frontend, or the LLM's own answer-processing logic).
- An `assistant_message` with **empty `generation->'tool_calls'`** even though
  the model clearly called a tool → this is the known turn-flush-drops-
  tool_calls issue (see [`decisions.md`](../decisions.md#card-reconstruction-reads-from-tool_result-never-assistant_messagetool_calls)).
  Don't try to reconstruct UI state from `tool_calls` for this reason.
- Check the actual `tool_result` JSON for `ask_human` — if `user_choice` looks
  like a template/placeholder rather than real data (e.g.
  `"Dine in • budget • food type • area • vibe"`), the *model* built a bad
  question/options, not a plumbing bug. See
  [`architecture/hitl.md`](../architecture/hitl.md) and the `AskHumanTool`
  description for the guardrail meant to prevent this.

To see the full `generation.tool_calls` JSON for an assistant message (useful
when checking whether calls were dropped):

```bash
docker exec agent-framework-postgres-1 psql -U postgres -d agentdb -t -c "
SELECT type, name, is_error, generation->'tool_calls' AS tcs
FROM steps
WHERE thread_id='<THREAD_ID>'
ORDER BY created_at;"
```

## Step 3 — if the backend genuinely never resumed

Check these in order:
1. **Is the tool marked `suspends = True`?** If not, the per-call timeout
   (`ChainPolicy.call_timeout_s`, default 60s) will have cancelled the
   suspended coroutine before your answer arrived. See
   [`decisions.md`](../decisions.md#tools-that-suspend-must-declare-suspends--true).
2. **Did the monolith process restart** between suspend and your click? See
   the durability gap in
   [`architecture/runtime-stages.md`](../architecture/runtime-stages.md#-known-gap-suspended-runs-are-not-actually-durable) —
   currently, a restart silently orphans any suspended run. There's no
   recovery for this yet; the fix is tracked in [`roadmap.md`](../roadmap.md) P0.
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
