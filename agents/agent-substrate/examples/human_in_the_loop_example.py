"""Example: Human-in-the-Loop agent interaction.

Demonstrates how the agent pauses to ask the user for input,
presents options, collects feedback, and continues execution.

Usage:
    python examples/human_in_the_loop_example.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from agent_framework.agents.react_agent import ReActAgent
from agent_framework.model_clients.openai.openai_client import OpenAIClient
from agent_framework.tools.builtin_tools import CalculatorTool, GetCurrentTimeTool
from agent_framework.human_input import CLIHumanHandler, AskHumanTool


async def main():
    # 1. Set up human input handler (CLI = terminal interaction)
    handler = CLIHumanHandler()

    # 2. Create the AskHuman tool (max 3 questions per run)
    ask_tool = AskHumanTool(
        handler=handler,
        max_requests_per_run=3,
    )

    # 3. Set up the agent with HITL support
    client = OpenAIClient(model="gpt-4.1-nano")

    agent = ReActAgent(
        name="hitl-assistant",
        description="An assistant that asks for human input when needed",
        model_client=client,
        tools=[ask_tool, CalculatorTool(), GetCurrentTimeTool()],
        system_instructions="""\
You are a helpful AI assistant. When you need the user's preference,
confirmation, or are choosing between multiple approaches, use the
ask_human tool to present 2-3 options and let them decide.

Guidelines for using ask_human:
- Present clear, distinct options (not just Yes/No when possible)
- Provide brief context explaining WHY you're asking
- The user always has a free-text "Other" option to write their own answer
- You can ask up to 3 questions per conversation
- After getting the user's answer, proceed accordingly
""",
        max_iterations=10,
    )

    # 4. Run the agent — it will pause when it needs human input
    print("\n--- Human-in-the-Loop Demo ---\n")

    result = await agent.run(
        "Help me plan a team dinner for 8 people this Friday."
    )

    print("\n--- Agent Result ---")
    print(result.output_text)
    print(f"\n{result.summary()}")

    # Show interaction history
    history = ask_tool.interaction_history
    if history:
        print(f"\n--- Human Interactions ({len(history)}) ---")
        for h in history:
            print(f"  Q: {h['question']}")
            print(f"  Options: {h['options']}")
            print(f"  Answer: {h['answer']} (freeform={h['is_freeform']})")
            print()


if __name__ == "__main__":
    asyncio.run(main())
