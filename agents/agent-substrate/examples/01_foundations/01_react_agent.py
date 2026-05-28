"""Example 1-1: ReAct Agent — demonstrates the ReAct loop with built-in tools."""

import asyncio

from ravi.configs.settings import settings
from ravi.extensions.agents.react.agent import ReActAgent
from ravi.extensions.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from ravi.integrations.llm.factory import create_model_client
from ravi.kernel.agent_catalog import AgentCatalog
from ravi.kernel.memory.unbounded_memory import UnboundedMemory
from ravi.kernel.messages._types import TextDeltaChunk


async def main() -> None:
    # --- 1. Setup & config
    chat_model = settings.CHAT_MODEL
    api_keys = {
        "openai":     settings.OPENAI_API_KEY,
        "anthropic":  settings.ANTHROPIC_API_KEY,
        "google":     settings.GEMINI_API_KEY,
        "groq":       settings.GROQ_API_KEY,
        "openrouter": settings.OPENROUTER_API_KEY,
    }
    print(f"CHAT_MODEL : {chat_model}")
    print(f"Keys ready : {[k for k, v in api_keys.items() if v]}")

    # --- 2. Build the catalog
    catalog = AgentCatalog()
    catalog.register_model("primary", create_model_client(chat_model, api_keys=api_keys))
    catalog.register_memory("memory", UnboundedMemory())
    for tool in [CalculatorTool(), GetCurrentTimeTool()]:
        catalog.register_tool(tool)

    # --- 3. Create ReActAgent
    agent = ReActAgent(
        name="DemoBot",
        description="A helpful assistant for demonstration.",
        catalog=catalog,
        max_iterations=5,
        verbose=True,
    )
    print(f"\nAgent '{agent.name}' ready with {len(catalog.all_tools())} tools.")

    # --- 4. Single-shot run
    print("\n--- Single-shot run ---")
    result = await agent.run(
        "What is the square root of 256 multiplied by 14? Also what time is it?"
    )
    print(f"Result: {result.output_text}")

    # --- 5. Streaming run
    print("\n--- Streaming run ---")
    await agent.reset()
    print("Response: ", end="", flush=True)
    async for chunk in agent.run_stream("Compute 15 factorial."):
        if isinstance(chunk, TextDeltaChunk):
            print(chunk.text, end="", flush=True)
    print()

    # --- 6. Multi-turn conversation
    print("\n--- Multi-turn conversation ---")
    await agent.reset()

    turn1 = await agent.run("My favourite number is 42. Remember that.")
    print(f"Turn 1: {turn1.output_text}")

    turn2 = await agent.run("What is my favourite number multiplied by 3?")
    print(f"Turn 2: {turn2.output_text}")

    turn3 = await agent.run("What time is it right now?")
    print(f"Turn 3: {turn3.output_text}")


if __name__ == "__main__":
    asyncio.run(main())
