"""Example 1-1: ReAct Agent with Tools and Skills

Demonstrates:
  • Built-in tools imported directly — no Toolbox needed
  • ToolSearchTool — agent discovers other tools and their parameters at runtime
  • SkillManager — discovers SKILL.md packages from capabilities/skills/
  • Console — interactive REPL with streaming output

Run:
    cd ravi-engine
    uv run examples/01_foundations/01_react_agent.py
"""

from __future__ import annotations

import asyncio

from ravi.config import settings
from ravi.agents.context import AgentContext, InMemoryHistoryProvider, SlidingWindowCompaction
from ravi.agents.runtime.local import LocalRuntime
from ravi.agents import ReActAgent
from ravi.kernel.tools import Tool
from ravi.kernel.skills import Skill
from ravi.adapters.llm.openai.openai_client import OpenAIClient
from ravi.capabilities.tools import CalculatorTool, CurrentTimeTool, WebSearchTool, ReadUrlTool
from ravi.capabilities.tools.tool_search import ToolSearchTool
from ravi.capabilities.internal.skill_manager import SkillManager
from ravi.console import Console


def build_agent(runtime: LocalRuntime) -> tuple[ReActAgent, SkillManager]:
    # Tools — plain list, no registry wrapper needed
    tools: list[Tool] = [
        CalculatorTool(),
        CurrentTimeTool(),
        WebSearchTool(),
        ReadUrlTool(),
    ]

    # ToolSearchTool lets the agent discover what tools exist and how to call them
    tool_search = ToolSearchTool(tools)

    # Skills — discover built-in SKILL.md packages whose tools are all registered
    skill_manager = SkillManager(auto_discover=True)
    registered_names = {t.name for t in tools}
    pre_loaded_skills: list[Skill] = []
    for meta in skill_manager._loader.all_metadata():
        if all(t in registered_names for t in meta.allowed_tools):
            pkg = skill_manager.activate(meta.name)
            if pkg:
                pre_loaded_skills.append(
                    Skill(
                        name=pkg.name,
                        instructions=pkg.body,
                        allowed_tools=pkg.metadata.allowed_tools,
                    )
                )

    model = OpenAIClient(
        model=settings.CHAT_MODEL.split("/")[-1],
        api_key=settings.OPENAI_API_KEY,
    )
    context = AgentContext(
        InMemoryHistoryProvider(),
        SlidingWindowCompaction(max_messages=40),
    )

    agent = ReActAgent(
        "DemoBot",
        runtime,
        model=model,
        tools=[*tools, tool_search],
        skills=pre_loaded_skills,
        context=context,
        system_instructions=(
            "You are a helpful assistant with access to tools. "
            "When a tool returns data, extract the answer directly from it — "
            "do not say you lack real-time access if the tool already provided the information. "
            "Always prefer using tools over relying on your training data for factual or time-sensitive questions."
        ),
        max_iterations=8,
    )

    return agent, skill_manager


async def main() -> None:
    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY not set — add it to ravi-engine/.env")

    async with LocalRuntime() as rt:
        agent, skill_manager = build_agent(rt)
        await Console(agent, skill_manager=skill_manager).interactive(stream=True)


if __name__ == "__main__":
    asyncio.run(main())
