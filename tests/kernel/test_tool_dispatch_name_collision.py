"""``ctx.tool(name, args)`` must never collide with a tool argument literally
called ``name``.

Real incident: the `skills` tool's `activate` action takes a `name` argument
(which skill to activate). ReActAgent's LLM-driven dispatch path calls
``ctx.tool(tc.tool_name, **tc.arguments)`` — with `**` splatting, the model
passing `{"action": "activate", "name": "excel-report"}` as that tool's
arguments crashed with `TypeError: tool() got multiple values for argument
'name'`, on every single call, the moment a real tool's schema happened to
use that name. Fixed by giving `tool()` one explicit `args: dict` parameter
instead of `**kwargs` — there is then no shared namespace between the
dispatcher's own parameters and a tool's arguments for ANY key to collide
with, not just `name`. This test reproduces the exact dispatch shape
(`ctx.tool(tc.tool_name, tc.arguments)`) ReActAgent uses, with a tool whose
schema has a `name` argument, mirroring `SkillTool`.
"""

from __future__ import annotations

import asyncio
from typing import Any

from substrate.types import TextBlock
from substrate.types import Actor
from substrate.runtime import DataPayload, Message
from substrate.types import RunLogKind
from substrate.tools import ToolExecutionResult, ToolRisk
from substrate.testing.runtime import ephemeral_runtime
from substrate.tools import Toolbox


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
    risk = ToolRisk.SAFE
    idempotent = True

    async def execute(self, *, action: str, name: str = "", **_: Any) -> ToolExecutionResult:
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=f"{action}:{name}")])


class _Caller:
    """Dispatches exactly as ReActAgent does and keeps what came back."""

    def __init__(self, calls: list[dict[str, Any]]) -> None:
        self.id = Actor("agent", "caller")
        self.tools = Toolbox()
        self.tools.add(_NameArgTool())
        self._calls = calls
        self.results: list[Any] = []

    async def run(self, ctx: Any, inbox: list[Message]) -> None:
        for arguments in self._calls:
            self.results.append(await ctx.tool("skills", arguments))


async def _dispatch(*calls: dict[str, Any]) -> list[Any]:
    agent = _Caller(list(calls))
    async with ephemeral_runtime() as runtime:
        await runtime.register(agent)
        run_id = await runtime.submit(agent.id, Message(target=agent.id, sender=Actor.system("test"), payload=DataPayload(data={})))
        async for entry in runtime.tail(run_id):
            assert entry.kind != RunLogKind.RUN_FAILED, entry.payload
            if entry.kind == RunLogKind.RUN_COMPLETED:
                break
    return agent.results


async def test_tool_call_with_a_name_shaped_argument_does_not_collide() -> None:
    """The exact ReActAgent dispatch shape: ctx.tool(tc.tool_name, tc.arguments)
    where tc.arguments itself contains a key called "name"."""
    (result,) = await asyncio.wait_for(_dispatch({"action": "activate", "name": "excel-report"}), 10)

    assert result.status == "ok"
    assert result.text == "activate:excel-report"


async def test_tool_call_with_no_name_argument_still_works() -> None:
    (result,) = await asyncio.wait_for(_dispatch({"action": "list"}), 10)

    assert result.status == "ok"
    assert result.text == "list:"
