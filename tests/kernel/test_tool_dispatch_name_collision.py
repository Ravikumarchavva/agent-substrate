"""``ctx.tool(name, args)`` must never collide with a tool argument literally
called ``name``.

Real incident: the `skills` tool's `activate` action takes a `name` argument
(which skill to activate). react.py's LLM-driven dispatch path calls
``ctx.tool(tc.tool_name, **tc.arguments)`` — with `**` splatting, the model
passing `{"action": "activate", "name": "excel-report"}` as that tool's
arguments crashed with `TypeError: tool() got multiple values for argument
'name'`, on every single call, the moment a real tool's schema happened to
use that name. Fixed by giving `tool()` one explicit `args: dict` parameter
instead of `**kwargs` — there is then no shared namespace between the
dispatcher's own parameters and a tool's arguments for ANY key to collide
with, not just `name`. This test reproduces the exact dispatch shape
(`ctx.tool(tc.tool_name, tc.arguments)`) react.py uses, with a tool whose
schema has a `name` argument, mirroring `SkillTool`.
"""

from __future__ import annotations

from typing import Any

from substrate.agents.runtime.backends._event_log import InMemoryEventLog
from substrate.agents.runtime.backends._fanout import PushAllFanout
from substrate.agents.runtime.backends._follow_graph import InMemoryFollowGraph
from substrate.agents.runtime.backends._inbox import InMemoryInbox
from substrate.agents.runtime.backends._scheduler import InMemoryScheduler
from substrate.agents.runtime.backends._signal_bus import InMemorySignalBus
from substrate.agents.runtime.backends._supervisor import InMemorySupervisor
from substrate.agents.runtime.cancellation import CancellationToken
from substrate.agents.runtime.context import RunContext
from substrate.agents.runtime.effect_cache import EffectCache
from substrate.agents.tools.invoker import ToolInvoker
from substrate.agents.tools.toolbox import Toolbox
from substrate.kernel.agent.runtime_context import RunMeta
from substrate.kernel.core.content import TextBlock
from substrate.kernel.runtime.ids import new_run_id
from substrate.kernel.tools import ToolExecutionResult


class _NameArgTool:
    """Same shape as SkillTool: one of its OWN arguments is called `name`."""

    name = "skills"
    description = "Toy tool with a same-named argument, mirroring SkillTool."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "name": {"type": "string"},
        },
        "required": ["action"],
    }

    async def execute(
        self, *, action: str, name: str = "", **_: Any
    ) -> ToolExecutionResult:
        return ToolExecutionResult(content=[TextBlock(text=f"{action}:{name}")])


async def _ctx_with(tool: Any) -> RunContext:
    event_log = InMemoryEventLog()
    run_id = new_run_id()
    inbox = InMemoryInbox()
    scheduler = InMemoryScheduler()
    signal_bus = InMemorySignalBus(scheduler)
    registry = Toolbox()
    registry.add(tool)
    meta = RunMeta(run_id=run_id, cancellation=CancellationToken())
    effect_cache = await EffectCache.fold(event_log, run_id)
    return RunContext(
        meta=meta,
        event_log=event_log,
        effect_cache=effect_cache,
        inbox=inbox,
        follow_graph=InMemoryFollowGraph(),
        fanout=PushAllFanout(),
        scheduler=scheduler,
        supervisor=InMemorySupervisor(event_log, inbox, scheduler, signal_bus),
        signal_bus=signal_bus,
        tool_invoker=ToolInvoker(registry=registry, approval_handler=None),
    )


async def test_tool_call_with_a_name_shaped_argument_does_not_collide():
    """The exact react.py dispatch shape: ctx.tool(tc.tool_name, tc.arguments)
    where tc.arguments itself contains a key called "name"."""
    ctx = await _ctx_with(_NameArgTool())
    tool_name = "skills"
    tool_arguments = {"action": "activate", "name": "excel-report"}

    result = await ctx.tool(tool_name, tool_arguments)

    assert result.status == "ok"
    assert result.text == "activate:excel-report"


async def test_tool_call_with_no_args_still_works():
    """args defaults to None/{} — the zero-argument call shape must be
    unaffected by dropping **kwargs."""
    ctx = await _ctx_with(_NameArgTool())

    result = await ctx.tool("skills", {"action": "list"})

    assert result.status == "ok"
    assert result.text == "list:"
