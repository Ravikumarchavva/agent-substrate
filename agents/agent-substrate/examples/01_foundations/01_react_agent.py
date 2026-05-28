"""Example 1-1: ReAct Agent

Demonstrates the ReAct (Reason + Act) loop with built-in tools.

Interactive streaming chat:
    uv run 01_react_agent.py
"""

import asyncio

from ravi.configs.settings import settings
from ravi.console import Console
from ravi.extensions.agents import UserProxyAgent, AssistantAgent
from ravi.kernel.agent_catalog import AgentCatalog
from ravi.integrations.llm.factory import LLMFactory
from ravi.kernel.memory.unbounded_memory import UnboundedMemory
from ravi.extensions.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool


# ---------------------------------------------------------------------------
# Agent factory
# ---------------------------------------------------------------------------


def _build_agent(model: str, api_key: str) -> AssistantAgent:
    llm = LLMFactory(model, api_key).build()
    catalog = AgentCatalog()
    catalog.register_model("primary", llm)
    catalog.register_memory("memory", UnboundedMemory())
    for tool in [CalculatorTool(), GetCurrentTimeTool()]:
        catalog.register_tool(tool)

    return AssistantAgent(
        name="DemoBot",
        description="A helpful assistant for demonstration.",
        catalog=catalog,
        max_iterations=5,
        verbose=False,
    )


async def main() -> None:

    api_key = settings.OPENAI_API_KEY
    if not api_key:
        raise SystemExit("OPENAI_API_KEY is not set. Add it to .env or the environment.")

    user = UserProxyAgent(name="User")
    agent = _build_agent(settings.CHAT_MODEL, api_key)

    con = Console(agent)
    await con.interactive(
        greeting=(
            f"[bold cyan]{agent.name}[/bold cyan] ready · "
            f"{len(agent.tools)} tools loaded\n"
            "[dim]Commands: /reset  /tools  /help  exit[/dim]"
        ),
        stream=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
