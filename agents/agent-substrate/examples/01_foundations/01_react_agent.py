"""Example 1-1: ReAct Agent with Tools

Demonstrates:
  • ReActAgent with built-in tools
  • Runtime as async context manager
  • Interactive REPL via the Console

Run:
    cd agent-substrate
    uv run examples/01_foundations/01_react_agent.py
"""

from __future__ import annotations

import asyncio

from dotenv import load_dotenv
from substrate.config import SubstrateConfig

load_dotenv()  # walks up to find the repo-root .env
settings = SubstrateConfig()

from substrate.agents import ReActAgent, Runtime
from substrate.agents.context import ContextConfig, InMemoryHistoryProvider, SlidingWindowCompaction, CompactionPipeline
from substrate.integrations.llm import (
    create_model_client,
    detect_provider,
    has_provider_api_key,
)
from substrate.capabilities.tools import CalculatorTool, CurrentTimeTool
from substrate.console import Console


async def main() -> None:
    provider = detect_provider(settings.CHAT_MODEL)
    if not has_provider_api_key(provider, settings.provider_keys):
        raise SystemExit(
            f"No API key for provider {provider!r} (model {settings.CHAT_MODEL!r}). "
            f"Add the matching key to agent-substrate/.env."
        )

    model = create_model_client(settings.CHAT_MODEL, api_keys=settings.provider_keys)
    agent = ReActAgent(
        "DemoBot",
        model=model,
        tools=[CalculatorTool(), CurrentTimeTool()],
        context=ContextConfig(InMemoryHistoryProvider(), pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=40)])),
        system_instructions=(
            "You are a helpful assistant with access to tools. "
            "Use a tool when it helps (e.g. calculations or the current time); "
            "otherwise just answer directly from your own knowledge. "
            "When a tool returns data, extract the answer directly from it."
        ),
        max_iterations=8,
    )

    async with Runtime() as rt:
        await rt.register(agent)
        con = Console(agent, runtime=rt)
        await con.interactive(stream=True)


if __name__ == "__main__":
    asyncio.run(main())
