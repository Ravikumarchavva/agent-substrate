"""Tools: let the agent act.

The easiest tool is a typed function with ``@tool``. The model sees a JSON schema built from the signature and the
docstring, and a call with bad arguments goes back to the model as an error instead of crashing the run. Two
declarations are required, because the engine acts on them:

* ``risk`` — whether a human must approve a call (``SAFE`` runs freely; ``HIGH`` and above wait for a yes).
* ``idempotent`` — whether a call that may or may not have happened (the process died mid-call) can safely be made
  again. If not, the run fails with the journaled intent for you to compensate from, rather than guessing.

A tool can also be a class (any object with ``name``, ``description``, ``input_schema``, ``risk``, ``idempotent`` and
``execute``) — see ``integrations/tools/compute/calculator.py``.

    uv run python examples/02_tools.py
"""

from __future__ import annotations

import asyncio

from _model import ToolCall, pick_model

from substrate.agents import ReActAgent
from substrate.integrations.tools.compute.calculator import CalculatorTool
from substrate.runtime import Runtime
from substrate.tools import ToolRisk, tool
from substrate.types import RunLogKind


@tool(risk=ToolRisk.SAFE, idempotent=True, concurrency_safe=True)  # a pure read: several calls in one turn may overlap
def word_count(text: str) -> int:
    """Count the words in a piece of text.

    Args:
        text: The text to count.
    """
    return len(text.split())


async def main() -> None:
    agent = ReActAgent(
        "analyst",
        model=pick_model(
            ToolCall("word_count", {"text": "to be or not to be"}),
            ToolCall("calculator", {"expression": "6 * 7"}),
            "The phrase has 6 words, and 6 * 7 is 42.",
        ),
        tools=[word_count, CalculatorTool()],
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
