"""04-1 — Combined Built-in Tools + Custom @tool Decorator

Demonstrates building a AssistantAgent with multiple built-in tools and a custom
inline tool defined via the @tool decorator.

Prerequisites: OPENAI_API_KEY set.
"""

import asyncio

from ravi.reasoning.agents.assistant import AssistantAgent
from ravi.fabric.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from ravi.adapters.llm.openai.openai_client import OpenAIClient
from ravi.kernel.agent_catalog import AgentCatalog
from ravi.fabric.memory.unbounded import UnboundedMemory
from ravi.kernel.tools import tool

# Infrastructure:
# - OPENAI_API_KEY environment variable required
# - No external services needed


def _make_catalog() -> AgentCatalog:
    from ravi.config import settings

    catalog = AgentCatalog()
    model_name = settings.CHAT_MODEL.split("/")[-1]
    catalog.register_model(
        "primary", OpenAIClient(model=model_name, api_key=settings.OPENAI_API_KEY)
    )
    catalog.register_memory("memory", UnboundedMemory())
    return catalog


async def main() -> None:

    # ---
    # Section 1: Agent with CalculatorTool + GetCurrentTimeTool

    catalog = _make_catalog()
    for t in [CalculatorTool(), GetCurrentTimeTool()]:
        catalog.register_tool(t)

    agent = AssistantAgent(
        name="assistant",
        description="Helpful assistant with calculator and clock tools",
        catalog=catalog,
        max_iterations=5,
    )

    # ---
    # Section 2: Single task that uses both tools in one query

    result = await agent.run("What is 1337 * 42? Also tell me the current UTC time.")
    print("=== Section 2: Combined tool call ===")
    print(result.output_text)
    print(result.summary())

    # ---
    # Section 3: Multi-turn conversation — memory is preserved across run() calls

    print("\n=== Section 3: Multi-turn (context retained) ===")
    r1 = await agent.run("My lucky number is 7. Remember it.")
    print("Turn 1:", r1.output_text)

    r2 = await agent.run("What is my lucky number multiplied by 6?")
    print("Turn 2:", r2.output_text)

    # Reset agent to start a fresh session
    await agent.reset()
    r3 = await agent.run("What is my lucky number?")
    print("Turn 3 (after reset — should not remember):", r3.output_text)

    # ---
    # Section 4: Custom inline tool via @tool decorator

    @tool
    async def celsius_to_fahrenheit(celsius: float) -> str:
        """Convert a temperature from Celsius to Fahrenheit."""
        fahrenheit = celsius * 9 / 5 + 32
        return f"{celsius}°C = {fahrenheit}°F"

    catalog2 = _make_catalog()
    catalog2.register_tool(celsius_to_fahrenheit)

    agent2 = AssistantAgent(
        name="converter",
        description="Temperature converter assistant",
        catalog=catalog2,
        max_iterations=5,
    )

    print("\n=== Section 4: Custom @tool ===")
    result2 = await agent2.run("Convert 100°C and -40°C to Fahrenheit.")
    print(result2.output_text)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
