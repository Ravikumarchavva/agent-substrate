"""Example 4-9: Orchestrator with 3 Specialist Sub-agents (Real LLM)

The orchestrator receives one compound query and delegates each part
to the right specialist:

  • researcher  — web_search  → finds current facts
  • calculator  — calculator  → does the maths
  • clock       — current_time → reads the current date/time

The orchestrator synthesises all three answers into one final reply.

Compound query used:
  "What is today's date, what is 1337 multiplied by 42,
   and who is the current CEO of OpenAI?"

Prerequisites:
    OPENAI_API_KEY set in ravi-engine/.env

Run:
    cd ravi-engine
    uv run examples/04_agents/09_orchestrator_real.py
"""

from __future__ import annotations

import asyncio

from agent_substrate.agents.context import ContextConfig, InMemoryHistoryProvider, SlidingWindowCompaction, CompactionPipeline
from agent_substrate.agents.core.react import ReActAgent
from agent_substrate.agents.core.orchestrator import OrchestratorAgent, SubAgentConfig
from agent_substrate.agents.runtime import Runtime
from agent_substrate.integrations.llm.openai.openai_client import OpenAIClient
from agent_substrate.capabilities.tools import CalculatorTool, CurrentTimeTool, WebSearchTool
from agent_substrate.console import Console
from agent_substrate.kernel import Priority
from agent_substrate.config import settings


def _model() -> OpenAIClient:
    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY not set — add it to ravi-engine/.env")
    return OpenAIClient(
        model=settings.CHAT_MODEL.split("/")[-1],
        api_key=settings.OPENAI_API_KEY,
    )


def _context() -> ContextConfig:
    return ContextConfig(
        InMemoryHistoryProvider(),
        pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=20)]),
    )


def build_team(runtime: Runtime) -> OrchestratorAgent:
    model = _model()

    # ── Specialist 1: web researcher ─────────────────────────────────────────
    researcher = ReActAgent(
        "researcher",
        model=model,
        system_instructions=(
            "You are a research specialist. Use web_search to find accurate, "
            "up-to-date information. Return a concise factual answer."
        ),
        tools=[WebSearchTool()],
        context=_context(),
        max_iterations=4,
    )

    # ── Specialist 2: calculator ──────────────────────────────────────────────
    calculator = ReActAgent(
        "calculator",
        model=model,
        system_instructions=(
            "You are a calculation specialist. Use the calculator tool for all "
            "arithmetic. Return only the computed result with brief explanation."
        ),
        tools=[CalculatorTool()],
        context=_context(),
        max_iterations=3,
    )

    # ── Specialist 3: clock ───────────────────────────────────────────────────
    clock = ReActAgent(
        "clock",
        model=model,
        system_instructions=(
            "You are a time specialist. Use current_time to get the exact "
            "current date and time. Return it in a clear, human-readable format."
        ),
        tools=[CurrentTimeTool()],
        context=_context(),
        max_iterations=2,
    )

    # ── Orchestrator ──────────────────────────────────────────────────────────
    return OrchestratorAgent(
        "coordinator",
        model=model,
        sub_agents=[
            SubAgentConfig(researcher, description="Searches the web for current facts and news.", priority=Priority.HIGH),
            SubAgentConfig(calculator, description="Performs precise numerical calculations.", priority=Priority.NORMAL),
            SubAgentConfig(clock, description="Reports the current date and time.", priority=Priority.NORMAL),
        ],
        max_iterations=12,
    )


QUERY = (
    "I need three things: "
    "1) What is today's date and time? "
    "2) What is 1337 multiplied by 42? "
    "3) Who is the current CEO of OpenAI?"
)


async def main() -> None:
    async with Runtime() as rt:
        orchestrator = build_team(rt)
        print(f"\nQuery: {QUERY}\n")
        await Console(orchestrator).run_stream(QUERY)


if __name__ == "__main__":
    asyncio.run(main())
