"""Tools: let the agent act.

A tool is any object with a name, a description, a JSON schema for its arguments, and an ``execute`` method. Two
declarations are required, because the engine acts on them:

* ``risk`` — whether a human must approve a call (``SAFE`` runs freely; ``HIGH`` and above wait for a yes).
* ``idempotent`` — whether a call that may or may not have happened (the process died mid-call) can safely be made
  again. If not, the run fails with the journaled intent for you to compensate from, rather than guessing.

    uv run python examples/02_tools.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from _model import ToolCall, pick_model

from substrate.agents import ReActAgent
from substrate.integrations.tools.compute.calculator import CalculatorTool
from substrate.runtime import Runtime
from substrate.tools import ToolExecutionResult, ToolRisk
from substrate.types import RunLogKind, TextBlock


class WordCount:
    name = "word_count"
    description = "Count the words in a piece of text."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "The text to count."}},
        "required": ["text"],
    }
    risk = ToolRisk.SAFE
    idempotent = True
    concurrency_safe = True  # a pure read: several calls in one turn may run at the same time

    async def execute(self, *, ctx: Any = None, text: str, **_: Any) -> ToolExecutionResult:
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=str(len(text.split())))])


async def main() -> None:
    agent = ReActAgent(
        "analyst",
        model=pick_model(
            ToolCall("word_count", {"text": "to be or not to be"}),
            ToolCall("calculator", {"expression": "6 * 7"}),
            "The phrase has 6 words, and 6 * 7 is 42.",
        ),
        tools=[WordCount(), CalculatorTool()],
        system_instructions="Use the tools for counting and arithmetic.",
    )

    async with Runtime.open("./.substrate") as runtime:
        outcome = await runtime.run(agent, "How many words are in 'to be or not to be', and what is 6 * 7?")
        print(outcome.output)

        # Everything the run did is in its journal — the calls, their arguments, and what came back.
        for entry in await runtime.read(outcome.run_id):
            if entry.kind in (RunLogKind.TOOL_CALL, RunLogKind.TOOL_RESULT):
                print(f"  {entry.kind:12} {entry.payload}")


if __name__ == "__main__":
    asyncio.run(main())
