"""Example 1-1: ReAct Agent with Tools

Demonstrates:
  • ReActAgent with built-in tools
  • Runtime as async context manager
  • Simple interactive REPL using run_agent() helper

Run:
    cd ravi-engine
    uv run examples/01_foundations/01_react_agent.py
"""

from __future__ import annotations

import asyncio

from agent_substrate.config import settings
from agent_substrate.agents import ReActAgent, Runtime
from agent_substrate.agents.context import ContextConfig, InMemoryHistoryProvider, SlidingWindowCompaction, CompactionPipeline
from agent_substrate.integrations.llm import LLMFactory
from agent_substrate.capabilities.tools import CalculatorTool, CurrentTimeTool, WebSearchTool, ReadUrlTool
from agent_substrate.kernel.core.content import ChatMessage, Role, TextBlock
from agent_substrate.kernel.core.identity import AgentId
from agent_substrate.kernel.messaging.message import Message, ChatPayload
from agent_substrate.console import Console


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
    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY not set — add it to ravi-engine/.env")

    model = LLMFactory(settings.CHAT_MODEL, settings.OPENAI_API_KEY).build()
    agent = ReActAgent(
        "DemoBot",
        model=model,
        tools=[CalculatorTool(), CurrentTimeTool(), WebSearchTool(), ReadUrlTool()],
        context=ContextConfig(InMemoryHistoryProvider(), pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=40)])),
        system_instructions=(
            "You are a helpful assistant with access to tools. "
            "When a tool returns data, extract the answer directly from it. "
            "Always prefer tools over training data for factual or time-sensitive questions."
        ),
        max_iterations=8,
    )

    async with Runtime() as rt:
        await rt.register(agent)
        con = Console(agent, runtime=rt)
        await con.interactive(stream=True)


if __name__ == "__main__":
    asyncio.run(main())
