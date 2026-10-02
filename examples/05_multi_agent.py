"""Several agents working together.

* ``OrchestratorAgent`` — a model that delegates: each sub-agent appears to it as a ``handoff_<name>`` tool, runs as
  its own durable run (with its own budget and journal), and its answer comes back as the tool's result.
* Flows — fixed coordination with no model in the middle: ``SequentialFlow``, ``ParallelFlow``, ``ConditionalFlow``.
  Anything with ``id`` and ``run(ctx, inbox)`` is an agent, so a step can be a plain class.

    uv run python examples/05_multi_agent.py
"""

from __future__ import annotations

import asyncio
from typing import Any

from _model import ToolCall, pick_model

from substrate.agents import OrchestratorAgent, ReActAgent, SequentialFlow, SubAgentConfig
from substrate.runtime import Runtime
from substrate.types import Actor


async def orchestrator_demo(runtime: Runtime) -> None:
    researcher = ReActAgent("researcher", model=pick_model("Rust: memory safety. Go: simple concurrency."))
    writer = ReActAgent("writer", model=pick_model("Rust gives safety; Go gives simplicity. Pick by team."))

    boss = OrchestratorAgent(
        "coordinator",
        model=pick_model(
            ToolCall("handoff_researcher", {"task": "Compare Rust and Go."}),
            ToolCall("handoff_writer", {"task": "Write two sentences from the findings."}),
            "Final answer: Rust gives safety; Go gives simplicity. Pick by team.",
        ),
        sub_agents=[
            SubAgentConfig(agent=researcher, description="Finds facts"),
            SubAgentConfig(agent=writer, description="Writes prose"),
        ],
    )
    outcome = await runtime.run(boss, "Research and summarise Rust vs Go.")
    print(outcome.output)


class Step:
    """A flow step: reply to whoever asked."""

    def __init__(self, key: str, says: str) -> None:
        self.id = Actor(type="agent", key=key)
        self._says = says

    async def run(self, ctx: Any, inbox: list[Any]) -> None:
        for message in inbox:
            await ctx.reply(message, {"text": self._says})


async def flow_demo(runtime: Runtime) -> None:
    fetch, analyse = Step("fetch", "Fetched 3 records."), Step("analyse", "All 3 records are valid.")
    pipeline = SequentialFlow(steps=[fetch, analyse], name="pipeline")
    for step in (fetch, analyse):
        await runtime.register(step)
    # Flows reply instead of streaming text, so they are invoked with ``ask``.
    outcome = await runtime.ask(pipeline, "Process the latest dataset.")
    print(outcome.output)


async def main() -> None:
    async with Runtime.open("./.substrate") as runtime:
        await orchestrator_demo(runtime)
        print("---")
        await flow_demo(runtime)


if __name__ == "__main__":
    asyncio.run(main())
