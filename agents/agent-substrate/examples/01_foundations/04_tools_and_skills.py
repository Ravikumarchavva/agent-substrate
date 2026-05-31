"""Example 1-4: Tools and Skills — Complete Guide
Module: ravi.kernel.tools.ToolRegistry, ravi.capabilities.tools.tool_search,
        ravi.capabilities.internal.skill_manager, ravi.agents.skills

Covers every layer of the tools+skills system:

  Part A — ToolRegistry
    1. Register tools, search by name/description
    2. Filter tools by risk level
    3. ToolSearchTool — agent-usable tool discovery

  Part B — SkillManager
    4. Discover SKILL.md packages from capabilities/skills/
    5. Lazy-load a full skill body on activation
    6. Inject into AssistantAgent via Skill dataclass

  Part C — Full agent session
    7. Build an agent with registry + skills
    8. Run with Console showing tool/skill panels

Run:
    cd ravi-engine
    uv run examples/01_foundations/04_tools_and_skills.py
"""

from __future__ import annotations

import asyncio
import random

from ravi.config import settings
from ravi.agents.context import AgentContext, InMemoryHistoryProvider
from ravi.agents.runtime.local import LocalRuntime
from ravi.agents.skills import Skill
from ravi.adapters.llm.openai.openai_client import OpenAIClient
from ravi.kernel import TextBlock, ToolExecutionResult
from ravi.kernel.tools import ToolRegistry, ToolRisk
from ravi.agents.assistant import AssistantAgent
from ravi.capabilities.internal.skill_manager import SkillManager
from ravi.capabilities.tools.tool_search import ToolSearchTool
from ravi.console import Console


# ===========================================================================
# Inline tool definitions
# ===========================================================================


class WeatherTool:
    name = "get_weather"
    description = "Return mock current weather for a city."
    risk = ToolRisk.SAFE
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "city": {"type": "string", "description": "City name"}
        },
        "required": ["city"],
    }

    async def execute(self, *, city: str, **_: object) -> ToolExecutionResult:
        # Mock data
        temp = random.randint(-10, 35)
        result = f"The current temperature in {city} is {temp}°C."
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=result)])


class SendEmailTool:
    name = "send_email"
    description = "Send an email (requires approval — marked HIGH risk)."
    risk = ToolRisk.HIGH
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["to", "subject", "body"],
    }

    async def execute(self, *, to: str, subject: str, body: str, **_: object) -> ToolExecutionResult:
        return ToolExecutionResult(
            name=self.name,
            content=[TextBlock(text=f"Email sent to {to}: {subject}")],
        )


class DeleteFileTool:
    name = "delete_file"
    description = "Permanently delete a file (CRITICAL — always needs approval)."
    risk = ToolRisk.CRITICAL
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }

    async def execute(self, *, path: str, **_: object) -> ToolExecutionResult:
        return ToolExecutionResult(
            name=self.name, content=[TextBlock(text=f"Deleted: {path}")]
        )


# ===========================================================================
# Part A — ToolRegistry
# ===========================================================================


def demo_tool_registry() -> None:
    print("\n" + "=" * 60)
    print("PART A — ToolRegistry")
    print("=" * 60)

    registry = ToolRegistry()
    weather = WeatherTool()
    email = SendEmailTool()
    delete = DeleteFileTool()

    registry.register(weather)
    registry.register(email)
    registry.register(delete)

    print(f"\n  Registered {len(registry)} tools: {registry.names()}")

    # Lookup by name
    t = registry.get("get_weather")
    print(f"  get('get_weather')  → {t.name if t else None}")

    # Filter by risk
    safe = registry.by_risk(ToolRisk.SAFE)
    high = registry.by_risk(ToolRisk.HIGH)
    critical = registry.by_risk(ToolRisk.CRITICAL)
    print(f"  SAFE tools     : {[t.name for t in safe]}")
    print(f"  HIGH tools     : {[t.name for t in high]}")
    print(f"  CRITICAL tools : {[t.name for t in critical]}")


async def demo_tool_search_tool() -> None:
    print("\n--- ToolSearchTool ---")

    registry = ToolRegistry()
    registry.register(WeatherTool())
    registry.register(SendEmailTool())
    registry.register(DeleteFileTool())

    search = ToolSearchTool(registry)
    registry.register(search)

    # Text mode — works with any LLM
    result = await search.execute(query="email")
    print(f"  text search('email'):\n    {result.text}")

    # Schema mode — for client-executed tool_search (OpenAI gpt-5.4+):
    # receive tool_search_call → run execute(format='schema') → return as tool_search_output
    result = await search.execute(query="weather", format="schema")
    import json
    schemas = json.loads(result.text)
    print(f"  schema search('weather') → {len(schemas['tools'])} tools with full schemas")
    if schemas["tools"]:
        print(f"    first schema keys: {list(schemas['tools'][0].keys())}")

    # Deferred format for OpenAI hosted tool_search (gpt-5.4+):
    # Pass registry.to_deferred_schemas() as the tools list — OpenAI handles search server-side.
    deferred = registry.to_deferred_schemas()
    tool_search_sentinel = next((t for t in deferred if t.get("type") == "tool_search"), None)
    deferred_fns = [t for t in deferred if t.get("type") != "tool_search"]
    print(f"\n  to_deferred_schemas(): {len(deferred_fns)} deferred functions + tool_search sentinel")
    print(f"    sentinel: {tool_search_sentinel}")
    print(f"    first deferred fn keys: {list(deferred_fns[0].keys())}")


# ===========================================================================
# Part B — SkillManager
# ===========================================================================


def demo_skill_manager() -> None:
    print("\n" + "=" * 60)
    print("PART B — SkillManager")
    print("=" * 60)

    manager = SkillManager(auto_discover=True)

    print(f"\n  Discovered {manager.skill_count} skills:")
    for meta in sorted(manager._loader.all_metadata(), key=lambda m: m.name):
        tools_hint = f"  [tools: {', '.join(meta.allowed_tools)}]" if meta.allowed_tools else ""
        print(f"    • {meta.name:<22} — {meta.description[:55]}...{tools_hint}")

    # System-prompt XML (lightweight — name + description only)
    xml = manager.available_skills_xml()
    lines = xml.splitlines()
    print(f"\n  available_skills_xml() → {len(lines)} lines, first skill:")
    for line in lines[1:6]:
        print(f"    {line}")

    # Lazy full load — only fetches body when activated
    pkg = manager.activate("code-review")
    if pkg:
        preview = pkg.body[:200].replace("\n", " ")
        print(f"\n  activate('code-review'):")
        print(f"    body preview: {preview!r}")
        print(f"    scripts : {pkg.list_scripts()}")
        print(f"    refs    : {pkg.list_references()}")

    # System prompt injection
    base = "You are a helpful assistant."
    enriched = manager.inject_into_prompt(base)
    extra_lines = enriched.count("\n") - base.count("\n")
    print(f"\n  inject_into_prompt() added {extra_lines} lines to system prompt")


# ===========================================================================
# Part C — Full agent session
# ===========================================================================


def _skills_compatible_with(
    skill_manager: SkillManager, registry: ToolRegistry
) -> list[Skill]:
    """Return Skill objects whose required tools are all present in registry.

    Skills that list tools the agent doesn't have confuse the LLM — it tries
    to follow the skill's procedure but can't call the missing tools.
    """
    available = set(registry.names())
    result: list[Skill] = []
    for meta in skill_manager._loader.all_metadata():
        if all(t in available for t in meta.allowed_tools):
            pkg = skill_manager.activate(meta.name)
            if pkg:
                result.append(
                    Skill(
                        name=pkg.name,
                        instructions=pkg.body,
                        allowed_tools=pkg.metadata.allowed_tools,
                    )
                )
    return result


def build_agent_with_skills(runtime: LocalRuntime) -> tuple[AssistantAgent, SkillManager]:
    """Build an AssistantAgent wired with ToolRegistry + SkillManager."""

    # Registry — all tools in one place
    registry = ToolRegistry()
    registry.register(WeatherTool())
    registry.register(SendEmailTool())
    registry.register(ToolSearchTool(registry))

    # Skills — only inject skills whose required tools are in the registry.
    # Skills referencing absent tools (e.g. web_search) confuse the LLM.
    skill_manager = SkillManager(auto_discover=True)
    pre_loaded = _skills_compatible_with(skill_manager, registry)
    compatible_names = [s.name for s in pre_loaded]
    print(f"\n  Compatible skills (tools satisfied): {compatible_names}")

    model = OpenAIClient(
        model=settings.CHAT_MODEL.split("/")[-1],
        api_key=settings.OPENAI_API_KEY,
    )
    agent = AssistantAgent(
        "SkillBot",
        runtime,
        model=model,
        tools=registry.all_tools(),
        skills=pre_loaded,
        context=AgentContext(InMemoryHistoryProvider()),
        max_iterations=6,
    )
    return agent, skill_manager


async def demo_agent_session() -> None:
    print("\n" + "=" * 60)
    print("PART C — Full agent session (interactive)")
    print("=" * 60)

    if not settings.OPENAI_API_KEY:
        print("  OPENAI_API_KEY not set — skipping interactive demo.")
        print("  Set it in ravi-engine/.env to run the full agent session.")
        return

    async with LocalRuntime() as rt:
        agent, skill_manager = build_agent_with_skills(rt)
        # Console shows /tools and /skills panels; tracks skill activation
        await Console(agent, skill_manager=skill_manager).interactive(stream=True)


# ===========================================================================
# Entry point
# ===========================================================================


async def main() -> None:
    demo_tool_registry()
    await demo_tool_search_tool()
    demo_skill_manager()
    await demo_agent_session()


if __name__ == "__main__":
    asyncio.run(main())
