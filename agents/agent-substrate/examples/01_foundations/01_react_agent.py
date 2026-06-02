"""Example 1-1: ReAct Agent with Tools and Skills
Module: ravi.agents.core, ravi.kernel.skills, ravi.capabilities.internal.skill_manager,
        ravi.kernel.tools.Toolbox

Demonstrates:
  • ToolRegistry — register tools once, pass the registry to the agent
  • ToolSearchTool — agent discovers other tools dynamically at runtime
  • SkillManager — discovers SKILL.md packages from capabilities/skills/
  • Skill injection — pre-load a skill into the agent's system prompt
  • Console — shows tools + skills at startup, tracks skill activation

Run:
    cd ravi-engine
    uv run examples/01_foundations/01_react_agent.py
"""

from __future__ import annotations

import asyncio
import datetime

from ravi.config import settings
from ravi.agents.context import AgentContext, InMemoryHistoryProvider, SlidingWindowCompaction
from ravi.agents.runtime.local import LocalRuntime
from ravi.agents import Skill
from ravi.adapters.llm.openai.openai_client import OpenAIClient
from ravi.kernel import TextBlock, ToolExecutionResult
from ravi.kernel.tools import Toolbox
from ravi.agents import ReActAgent
from ravi.capabilities.internal.skill_manager import SkillManager
from ravi.console import Console


# ---------------------------------------------------------------------------
# Inline tools — satisfy Tool Protocol structurally (no base class needed)
# ---------------------------------------------------------------------------


class CalculatorTool:
    name = "calculator"
    description = "Evaluate a Python arithmetic expression and return the result."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "e.g. '(42 * 3) / 7' or '2 ** 10'",
            }
        },
        "required": ["expression"],
    }

    async def execute(self, *, expression: str, **_: object) -> ToolExecutionResult:
        try:
            result = eval(expression, {"__builtins__": {}})  # noqa: S307
            return ToolExecutionResult(
                name=self.name, content=[TextBlock(text=str(result))]
            )
        except Exception as exc:
            return ToolExecutionResult(
                name=self.name,
                content=[TextBlock(text=f"Error: {exc}")],
                is_error=True,
            )


class GetCurrentTimeTool:
    name = "get_current_time"
    description = "Return the current UTC date and time as ISO-8601."
    input_schema: dict[str, object] = {"type": "object", "properties": {}}

    async def execute(self, **_: object) -> ToolExecutionResult:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return ToolExecutionResult(
            name=self.name, content=[TextBlock(text=now)]
        )


# ---------------------------------------------------------------------------
# Agent setup
# ---------------------------------------------------------------------------


def build_agent(runtime: LocalRuntime) -> tuple[ReActAgent, SkillManager]:
    # 1. Toolbox — register all tools
    toolbox = Toolbox()
    toolbox.add(CalculatorTool())
    toolbox.add(GetCurrentTimeTool())

    # 2. Skills — discover built-in SKILL.md packages
    #    Only inject skills whose allowed_tools are all registered.
    #    Skills referencing missing tools confuse the LLM.
    skill_manager = SkillManager(auto_discover=True)
    registered_names = set(toolbox.names())
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

    # 4. LLM + context
    model = OpenAIClient(
        model=settings.CHAT_MODEL.split("/")[-1],
        api_key=settings.OPENAI_API_KEY,
    )
    context = AgentContext(
        InMemoryHistoryProvider(),
        [SlidingWindowCompaction(max_messages=40)],
    )

    # 5. Agent — receives the tool list and pre-loaded skills
    agent = ReActAgent(
        "DemoBot",
        runtime,
        model=model,
        tools=toolbox.all(),
        skills=pre_loaded_skills,
        context=context,
        max_iterations=8,
    )

    return agent, skill_manager


# ---------------------------------------------------------------------------
# Main — interactive REPL
# ---------------------------------------------------------------------------


async def main() -> None:
    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY not set — add it to ravi-engine/.env")

    async with LocalRuntime() as rt:
        agent, skill_manager = build_agent(rt)

        # Console receives the skill_manager so it can show /skills info
        await Console(agent, skill_manager=skill_manager).interactive(stream=True)


if __name__ == "__main__":
    asyncio.run(main())
