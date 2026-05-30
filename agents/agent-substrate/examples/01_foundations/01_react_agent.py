"""Example 1-1: ReAct Agent

Demonstrates the ReAct (Reason + Act) loop with built-in tools.

Interactive streaming chat:
    uv run 01_react_agent.py
"""

import asyncio

from ravi.configs.settings import settings
from ravi.console import Console
from ravi.fabric.runtime import LocalRuntime
from ravi.reasoning.agents.assistant import AssistantAgent
from ravi.fabric.agents_base import AgentContext
from ravi.integrations.llm.factory import LLMFactory
from ravi.fabric.memory.in_memory import InMemoryHistoryProvider
from ravi.fabric.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool


# Build agent catalog and assistant agent instance


def _build_agent(model: str, api_key: str, runtime: LocalRuntime) -> AssistantAgent:

    return AssistantAgent(
        name="DemoBot",
        description="A helpful assistant for demonstration.",
        tools=[CalculatorTool(), GetCurrentTimeTool()],
        context=AgentContext(
            history=InMemoryHistoryProvider()
        ),
        model=LLMFactory(model, api_key).build(),
        max_iterations=5,
        runtime=runtime,
        verbose=False,
    )


async def main() -> None:

    api_key = settings.OPENAI_API_KEY
    if not api_key:
        raise SystemExit(
            "OPENAI_API_KEY is not set. Add it to .env or the environment."
        )

    runtime = LocalRuntime()
    await runtime.start()

    try:
        agent = _build_agent(settings.CHAT_MODEL, api_key, runtime)
        await agent.start()

        con = Console(agent)
        await con.interactive(
            greeting=(
                "[bold cyan]User[/bold cyan] ready · "
                f"{len(agent.tools)} tools loaded\n"
                "[dim]Commands: /reset  /tools  /help  exit[/dim]"
            ),
            stream=True,
        )
    finally:
        await runtime.stop()


if __name__ == "__main__":
    asyncio.run(main())
