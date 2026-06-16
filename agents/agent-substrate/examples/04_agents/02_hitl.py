"""04-2 — Human-in-the-Loop (HITL) Agent

Demonstrates an agent that pauses execution to ask the human for input at
decision points using AskHumanTool.

Prerequisites: OPENAI_API_KEY set.
"""

from __future__ import annotations

import asyncio
import uuid

from ravi.capabilities.tools.human_input import AskHumanTool, HumanInputResponse
from ravi.agents import ReActAgent, Runtime
from ravi.agents.context import ContextConfig, InMemoryHistoryProvider, SlidingWindowCompaction, CompactionPipeline
from ravi.capabilities.tools import CalculatorTool
from ravi.integrations.llm import LLMFactory
from ravi.kernel.core.content import ChatMessage, Role, TextBlock
from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import Message, ChatPayload


async def run_agent(rt: Runtime, agent: ReActAgent, text: str, *, session_id: str) -> str:
    msg = Message(
        target=agent.id,
        sender=AgentId(type="proxy", key="user"),
        payload=ChatPayload(message=ChatMessage(role=Role.USER, content=[TextBlock(text=text)])),
        correlation_id=session_id,
    )
    run_id = await rt.submit(agent.id, msg)
    async for entry in rt.event_log.tail(run_id):
        if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
            break
    history = await agent.history.get_messages(agent.id, session_id=session_id)
    for m in reversed(history):
        if m.role == Role.ASSISTANT:
            return " ".join(b.text for b in m.content if isinstance(b, TextBlock) and b.text)
    return ""


async def main() -> None:
    # ---
    # Section 1: Setup CLIHumanHandler + AskHumanTool (max 3 questions)

    class MockHumanHandler:
        async def request_input(self, request) -> HumanInputResponse:
            print(f"\n[Mocked Human Input Request]\n  Q: {request.question}")
            return HumanInputResponse(
                request_id=request.request_id,
                selected_label="Italian cuisine, $50 per person budget, casual style.",
            )

    handler = MockHumanHandler()
    ask_tool = AskHumanTool(handler=handler, max_requests_per_run=3)  # type: ignore[arg-type]

    # ---
    # Section 2: Build ReActAgent with AskHumanTool + CalculatorTool

    from ravi.config import settings

    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY not set — add it to ravi-engine/.env")

    model = LLMFactory(settings.CHAT_MODEL, settings.OPENAI_API_KEY).build()
    session_id = uuid.uuid4().hex

    agent = ReActAgent(
        "hitl-assistant",
        model=model,
        tools=[ask_tool, CalculatorTool()],
        context=ContextConfig(InMemoryHistoryProvider(), pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=40)])),
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
    async with Runtime() as rt:
        await rt.register(agent)
        output = await run_agent(rt, agent, "Help me plan a team dinner for 8 people this Friday.", session_id=session_id)
        print("\n=== Final Plan ===")
        print(output)

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


if __name__ == "__main__":
    asyncio.run(main())
