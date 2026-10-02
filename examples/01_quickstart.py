"""Quickstart: one agent, one question.

    uv run python examples/01_quickstart.py

An agent is a definition: a model, instructions, tools. A ``Runtime`` runs it — durably, over a store in a
folder — and ``run`` gives back the answer. Nothing to start first: no database server, no queue, no config file.
"""

from __future__ import annotations

import asyncio

from _model import pick_model

from substrate.agents import ReActAgent
from substrate.runtime import Runtime


async def main() -> None:
    agent = ReActAgent(
        "assistant",
        model=pick_model("The capital of France is Paris."),
        system_instructions="You are a concise assistant.",
    )

    # ``Runtime.open`` opens the store in ./.substrate (created if it is not there) and closes it on exit.
    async with Runtime.open("./.substrate") as runtime:
        outcome = await runtime.run(agent, "What is the capital of France?")

    print(outcome.status.value, "-", outcome.output)


if __name__ == "__main__":
    asyncio.run(main())
