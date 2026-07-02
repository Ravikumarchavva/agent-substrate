# Playbook: Adding a Tool

Root `CLAUDE.md` has the minimal skeleton. This fills in the details that
matter once the tool does anything beyond a trivial computation.

## Base skeleton (from root CLAUDE.md)

```python
from substrate.kernel.tools import ToolExecutionResult
from substrate.kernel.core.content import TextBlock

class MyTool:
    name = "my_tool"
    description = "What it does"
    input_schema = {"type": "object", "properties": {...}, "required": [...]}

    async def execute(self, *, ctx=None, **kwargs) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text="result")])
```

Placed at `capabilities/tools/my_tool/tool.py` — `CatalogScanner` discovers it
automatically, no registration needed.

## Decision points before you write it

### 1. Does it need `ctx`? What's the real type?

`ctx: RunContext | None = None` — not `object`. `RunContext` lives at
`agents/runtime/context.py` (L1), and `capabilities` (L2) is allowed to import
it since L2 sits above L1. Import it under `TYPE_CHECKING` if you only need it
for the annotation and want to avoid any runtime import surface:

```python
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from substrate.agents.runtime.context import RunContext

async def execute(self, *, ctx: "RunContext | None" = None, **kwargs) -> ToolExecutionResult:
    ...
```

`ctx` gives you `ctx.run_id`, `ctx._log(kind, payload)` (writes to the
EventLog — use this for anything the UI needs to stream live), and suspension
primitives (`ctx.sleep_until_signal`, `ctx.sleep_until`).

### 2. Does it suspend the run waiting on something external?

If yes (a human, a timer, another agent's completion) — **this changes two
things**:

```python
class MyTool:
    suspends: bool = True   # exempts you from ToolInvoker's per-call timeout
```

and inside `execute()`:

```python
await ctx._log("my.event.requested", {...})   # so the UI can render something
payload = await ctx.sleep_until_signal(f"my_signal:{some_id}")
# resume here once someone calls SignalBus.signal(run_id, f"my_signal:{some_id}", payload)
```

Follow the `ask_human` pattern exactly (`capabilities/tools/human_input.py`)
rather than inventing a new suspension mechanism — see
[`decisions.md`](../decisions.md#suspension-uses-signalbussignal-never-asynciofuture)
for why Futures are explicitly ruled out here.

**If you skip the `suspends` flag**, your suspending tool will get cancelled
by `asyncio.wait_for(..., timeout=60s)` in `ToolInvoker` the moment a human
takes longer than a minute to respond — silently dropping their answer. This
is a real bug that shipped once already; don't reproduce it.

### 3. What risk level?

`risk: str` on the tool class — one of `"safe"`, `"sensitive"`, `"critical"`
(see `kernel/tools/tools.py` `ToolRisk`). This drives:
- Whether the tool needs approval before executing
  (`tools_requiring_approval` on the agent)
- The color badge shown in the UI (`_build_tool_meta_map` in
  `serving/monolith/routes/chat.py`: critical=red, high=yellow, else green)

Examples from the codebase: `ask_human` is `"safe"` (it *is* the human — no
separate approval needed even though it suspends). `WebSurferTool` and both
code-interpreter tools are `"critical"`/`"sensitive"` and are flagged
(`# TODO: L4-hitl`) as needing migration to signal-based approval — see
[`roadmap.md`](../roadmap.md) P1.

### 4. Does it need to stream live UI updates (not just a final result)?

Use `ctx._log(kind, payload)` with a `kind` string that matches a wire event
type (`serving/protocol/events.py`), and make sure that kind is in
`STREAMING_KINDS` (`serving/protocol/from_log.py`) if you want it to flow
through the normal SSE tail automatically. This is how `ask_human`'s
`input.requested` event reaches the frontend without a separate out-of-band
channel.

### 5. Does its result need to survive a page reload as rich UI (not just text)?

**Embed everything the UI needs to reconstruct itself directly in the tool's
result JSON.** Don't rely on the assistant turn's `tool_calls` being present
on reload — see
[`decisions.md`](../decisions.md#card-reconstruction-reads-from-tool_result-never-assistant_messagetool_calls)
for why that's unreliable. `AskHumanTool._shape_result()` is the reference
pattern: it embeds a `_card` key with everything needed to redraw the card,
inside the `tool_result` payload itself.

## Checklist before shipping a new suspending tool

- [ ] `suspends = True` set
- [ ] Uses `ctx.sleep_until_signal()`, not a Future
- [ ] Every possible resume outcome returns a valid `ToolExecutionResult`
      (no `tool_use` left without a `tool_result`)
- [ ] Result JSON embeds whatever the UI needs to reconstruct itself on reload
- [ ] `risk` level set appropriately
- [ ] If it's a fourth HITL-shaped mechanism, ask whether it should just be
      `ask_human` with different framing instead — check
      [`architecture/hitl.md`](../architecture/hitl.md) first
