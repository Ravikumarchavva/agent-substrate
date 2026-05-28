"""04-2 — Human-in-the-Loop (HITL) Agent

Demonstrates an agent that pauses execution to ask the human for input at
decision points using AskHumanTool.

The agent uses CLIHumanHandler which reads from stdin. In production, replace
CLIHumanHandler with a web handler that goes through the API (e.g. a
WebSocket-backed CallbackHumanHandler tied to your HTTP endpoint).

Prerequisites: OPENAI_API_KEY set.
"""

import asyncio

from ravi.catalog.tools.human_input.tool import AskHumanTool, CLIHumanHandler
from ravi.extensions.agents.react.agent import ReActAgent
from ravi.extensions.tools.builtin_tools import CalculatorTool
from ravi.integrations.llm.openai.openai_client import OpenAIClient
from ravi.kernel.agent_catalog import AgentCatalog
from ravi.kernel.memory.unbounded_memory import UnboundedMemory

# Infrastructure:
# - OPENAI_API_KEY environment variable required
# - No external services needed


async def main() -> None:

    # ---
    # Section 1: Setup CLIHumanHandler + AskHumanTool (max 3 questions)

    handler = CLIHumanHandler()
    ask_tool = AskHumanTool(handler=handler, max_requests_per_run=3)

    # ---
    # Section 2: Build catalog with system prompt directing the agent to ask humans

    catalog = AgentCatalog()
    catalog.register_model("primary", OpenAIClient(model="gpt-4.1-nano"))
    catalog.register_memory("memory", UnboundedMemory())
    for t in [ask_tool, CalculatorTool()]:
        catalog.register_tool(t)

    agent = ReActAgent(
        name="hitl-assistant",
        description="Dinner planning assistant that checks preferences with the user",
        catalog=catalog,
        system_instructions=(
            "You are a helpful event-planning assistant. Whenever you need the "
            "user's preference or confirmation — such as cuisine, dietary "
            "restrictions, venue style, or budget — use the ask_human tool to "
            "present 2–3 clear options. You may ask up to 3 questions per run."
        ),
        max_iterations=10,
    )

    # ---
    # Section 3: Run the planning task

    print("=== Team Dinner Planner (answer the prompts below) ===\n")
    result = await agent.run(
        "Help me plan a team dinner for 8 people this Friday."
    )
    print("\n=== Final Plan ===")
    print(result.output_text)

    # ---
    # Section 4: Print interaction_history — questions asked and answers given

    history = ask_tool.interaction_history
    if history:
        print(f"\n=== Human Interactions ({len(history)}) ===")
        for entry in history:
            print(f"  Q: {entry.get('question', '')}")
            print(f"  A: {entry.get('answer', '')}")
    else:
        print("\n(No human interactions recorded.)")

    # ---
    # Production note: replace CLIHumanHandler with a web handler:
    #
    #   from ravi.catalog.tools.human_input.tool import CallbackHumanHandler
    #
    #   async def web_handler(request):
    #       # push request to frontend via WebSocket / SSE, await reply
    #       ...
    #
    #   handler = CallbackHumanHandler(callback=web_handler)
    #   ask_tool = AskHumanTool(handler=handler)


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

