"""Example 1-4: Tools — Complete Guide
Module: substrate.agents.tools.toolbox.Toolbox, substrate.kernel.tools.ToolRisk

Covers the tools system:

  Part A — Toolbox
    1. Add tools, lookup by name/description
    2. Filter tools by risk level
    3. deferred_schemas() for OpenAI hosted tool_search

  Part B — Full agent session with Toolbox
    4. Build a ReActAgent wired with Toolbox
    5. Run a question through it

Run:
    cd agent-substrate
    uv run examples/01_foundations/04_tools_and_skills.py
"""

from __future__ import annotations

from dotenv import load_dotenv
from substrate.config import SubstrateConfig

load_dotenv()  # walks up to find the repo-root .env
settings = SubstrateConfig()


import asyncio
import random
from substrate.agents import ReActAgent, Runtime
from substrate.agents.context import ContextConfig, InMemoryHistoryProvider, SlidingWindowCompaction, CompactionPipeline
from substrate.agents.tools.toolbox import Toolbox
from substrate.integrations.llm import (
    create_model_client,
    detect_provider,
    has_provider_api_key,
)
from substrate.kernel import TextBlock, ToolExecutionResult
from substrate.kernel.core.content import ChatMessage, Role
from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import Message, ChatPayload
from substrate.kernel.tools import ToolRisk


# ===========================================================================
# Inline tool definitions
# ===========================================================================


class WeatherTool:
    name = "get_weather"
    description = "Return mock current weather for a city."
    risk = ToolRisk.SAFE
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"city": {"type": "string", "description": "City name"}},
        "required": ["city"],
    }

    async def execute(self, *, city: str, **_: object) -> ToolExecutionResult:
        temp = random.randint(-10, 35)
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=f"The current temperature in {city} is {temp}°C.")])


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
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=f"Email sent to {to}: {subject}")])


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
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=f"Deleted: {path}")])


# ===========================================================================
# Part A — Toolbox
# ===========================================================================


def demo_toolbox() -> None:
    print("\n" + "=" * 60)
    print("PART A — Toolbox")
    print("=" * 60)

    registry = Toolbox()
    registry.add(WeatherTool())
    registry.add(SendEmailTool())
    registry.add(DeleteFileTool())

    print(f"\n  Registered {len(registry.all())} tools: {registry.names()}")

    t = registry.get("get_weather")
    print(f"  get('get_weather')  → {t.name if t else None}")

    safe = registry.by_risk(ToolRisk.SAFE)
    high = registry.by_risk(ToolRisk.HIGH)
    critical = registry.by_risk(ToolRisk.CRITICAL)
    print(f"  SAFE tools     : {[t.name for t in safe]}")
    print(f"  HIGH tools     : {[t.name for t in high]}")
    print(f"  CRITICAL tools : {[t.name for t in critical]}")

    deferred = registry.deferred_schemas()
    print(f"\n  deferred_schemas() → {len(deferred)} entries")
    if deferred:
        print(f"    first entry keys: {list(deferred[0].keys())}")


# ===========================================================================
# Part B — Full agent session
# ===========================================================================


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


async def demo_agent_session() -> None:
    print("\n" + "=" * 60)
    print("PART B — Full agent session")
    print("=" * 60)

    provider = detect_provider(settings.CHAT_MODEL)
    if not has_provider_api_key(provider, settings.provider_keys):
        print(f"  No API key for provider {provider!r} — skipping agent demo.")
        print("  Set the matching key in agent-substrate/.env to run the full session.")
        return

    registry = Toolbox()
    registry.add(WeatherTool())
    registry.add(SendEmailTool())

    model = create_model_client(settings.CHAT_MODEL, api_keys=settings.provider_keys)
    agent = ReActAgent(
        "ToolBot",
        model=model,
        tools=registry.all(),
        context=ContextConfig(InMemoryHistoryProvider(), pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=20)])),
        system_instructions="You are a helpful assistant. Use the available tools to answer questions.",
        max_iterations=6,
    )

    async with Runtime() as rt:
        await rt.register(agent)
        output = await run_agent(rt, agent, "What is the weather in Tokyo?", session_id="tool-demo")
    print(f"\n  Q: What is the weather in Tokyo?")
    print(f"  A: {output!r}")


# ===========================================================================
# Entry point
# ===========================================================================


async def main() -> None:
    demo_toolbox()
    await demo_agent_session()


if __name__ == "__main__":
    asyncio.run(main())
