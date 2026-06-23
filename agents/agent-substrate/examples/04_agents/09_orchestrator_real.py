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
    OPENAI_API_KEY set in agent-substrate/.env

Run:
    cd agent-substrate
    uv run examples/04_agents/09_orchestrator_real.py
"""

from __future__ import annotations

from dotenv import load_dotenv
from substrate.config import SubstrateConfig

load_dotenv()  # walks up to find the repo-root .env
settings = SubstrateConfig()


import asyncio

from substrate.agents.context import ContextConfig, InMemoryHistoryProvider, SlidingWindowCompaction, CompactionPipeline
from substrate.agents.core.react import ReActAgent
from substrate.agents.core.orchestrator import OrchestratorAgent, SubAgentConfig
from substrate.agents.runtime import Runtime
from substrate.integrations.llm import (
    create_model_client,
    detect_provider,
    has_provider_api_key,
)
from substrate.capabilities.tools import CalculatorTool, CurrentTimeTool, WebSearchTool
from substrate.console import Console
from substrate.kernel import Priority


def _model():
    provider = detect_provider(settings.CHAT_MODEL)
    if not has_provider_api_key(provider, settings.provider_keys):
        raise SystemExit(
            f"No API key for provider {provider!r} (model {settings.CHAT_MODEL!r}). "
            f"Add the matching key to agent-substrate/.env."
        )
    return create_model_client(settings.CHAT_MODEL, api_keys=settings.provider_keys)


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
        # Register every sub-agent so the runtime can resolve them when the
        # orchestrator spawns them. Without this, each spawned run finds no
        # agent in the registry and the orchestrator's ctx.ask() blocks until
        # its timeout (120s per sub-agent).
        for sub_cfg in orchestrator._sub_agents:
            await rt.register(sub_cfg.agent)
        print(f"\nQuery: {QUERY}\n")
        await Console(orchestrator, runtime=rt).run_stream(QUERY)


if __name__ == "__main__":
    asyncio.run(main())
