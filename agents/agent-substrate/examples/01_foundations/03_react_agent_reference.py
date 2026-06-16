"""Example 1-3: ReAct Agent — Full Reference

Demonstrates:
  1. Basic single-turn run via Runtime
  2. Multi-turn conversation via shared history
  3. UserProxyAgent → ReActAgent via Runtime
  4. OrchestratorAgent delegating to specialist sub-agents
  5. Interactive REPL with OrchestratorAgent

Run:
    cd ravi-engine
    OPENAI_API_KEY=sk-... uv run examples/01_foundations/03_react_agent_reference.py
"""

from __future__ import annotations

import asyncio
import datetime
import math

from ravi.config import settings
from ravi.agents import ReActAgent, OrchestratorAgent, SubAgentConfig, UserProxyAgent, Runtime
from ravi.agents.context import ContextConfig, InMemoryHistoryProvider, SlidingWindowCompaction, CompactionPipeline
from ravi.integrations.llm import LLMFactory
from ravi.kernel import TextBlock, ToolExecutionResult
from ravi.kernel.core.content import ChatMessage, Role
from ravi.kernel.core.identity import AgentId
from ravi.kernel.messaging.message import Message, ChatPayload


# ---------------------------------------------------------------------------
# Shared helper
# ---------------------------------------------------------------------------


async def run_agent(rt: Runtime, agent: ReActAgent, text: str, *, session_id: str | None = None) -> str:
    sid = session_id or agent.id.key
    msg = Message(
        target=agent.id,
        sender=AgentId(type="proxy", key="user"),
        payload=ChatPayload(message=ChatMessage(role=Role.USER, content=[TextBlock(text=text)])),
        correlation_id=sid,
    )
    run_id = await rt.submit(agent.id, msg)
    async for entry in rt.event_log.tail(run_id):
        if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
            break
    history = await agent.history.get_messages(agent.id, session_id=sid)
    for m in reversed(history):
        if m.role == Role.ASSISTANT:
            return " ".join(b.text for b in m.content if isinstance(b, TextBlock) and b.text)
    return ""


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class MathTool:
    name = "math"
    description = "Evaluate a Python math expression. You may use any function from the `math` module."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {"expression": {"type": "string", "description": "e.g. 'math.sqrt(144)'"}},
        "required": ["expression"],
    }

    async def execute(self, *, expression: str, **_kw: object) -> ToolExecutionResult:
        try:
            result = eval(expression, {"math": math, "__builtins__": {}})  # noqa: S307
            return ToolExecutionResult(name=self.name, content=[TextBlock(text=str(result))])
        except Exception as exc:
            return ToolExecutionResult(name=self.name, content=[TextBlock(text=str(exc))], is_error=True)


class ClockTool:
    name = "clock"
    description = "Return the current UTC timestamp."
    input_schema: dict[str, object] = {"type": "object", "properties": {}}

    async def execute(self, **_kw: object) -> ToolExecutionResult:
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=ts)])


# ---------------------------------------------------------------------------
# 1. Basic single-turn run
# ---------------------------------------------------------------------------


async def demo_basic_run() -> None:
    print("=== 1. Basic single-turn run ===")

    model = LLMFactory(settings.CHAT_MODEL, settings.OPENAI_API_KEY).build()
    agent = ReActAgent(
        "Calculator",
        model=model,
        tools=[MathTool()],
        context=ContextConfig(InMemoryHistoryProvider(), pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=20)])),
        system_instructions="You are a maths assistant. Use the math tool for any calculation.",
        max_iterations=6,
    )

    async with Runtime() as rt:
        await rt.register(agent)
        output = await run_agent(rt, agent, "What is math.sqrt(256) * math.pi?")
    print(f"  output : {output!r}")


# ---------------------------------------------------------------------------
# 2. Multi-turn conversation via shared history
# ---------------------------------------------------------------------------


async def demo_multi_turn() -> None:
    print("\n=== 2. Multi-turn conversation ===")

    model = LLMFactory(settings.CHAT_MODEL, settings.OPENAI_API_KEY).build()
    agent = ReActAgent(
        "Tutor",
        model=model,
        tools=[MathTool()],
        context=ContextConfig(InMemoryHistoryProvider(), pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=40)])),
        max_iterations=6,
    )

    turns = [
        "My name is Ada. Remember that.",
        "What is 7 * 8?",
        "What is my name and what was the result I asked for?",
    ]
    session = "tutor-session"
    async with Runtime() as rt:
        await rt.register(agent)
        for q in turns:
            output = await run_agent(rt, agent, q, session_id=session)
            print(f"  Q: {q!r}")
            print(f"  A: {output!r}")


# ---------------------------------------------------------------------------
# 3. UserProxyAgent → ReActAgent via Runtime
# ---------------------------------------------------------------------------


async def demo_proxy() -> None:
    print("\n=== 3. UserProxyAgent → ReActAgent ===")

    model = LLMFactory(settings.CHAT_MODEL, settings.OPENAI_API_KEY).build()
    backend = ReActAgent(
        "Backend",
        model=model,
        tools=[MathTool(), ClockTool()],
        context=ContextConfig(InMemoryHistoryProvider(), pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=20)])),
        max_iterations=6,
    )

    async with Runtime() as rt:
        await rt.register(backend)
        proxy = UserProxyAgent("proxy", rt, key="user-1")
        output = await proxy.ask("What is math.factorial(10)?", recipient=backend.id)
    print(f"  proxy.ask result : {output!r}")


# ---------------------------------------------------------------------------
# 4. OrchestratorAgent — delegates to specialist sub-agents
# ---------------------------------------------------------------------------


async def demo_orchestrator() -> None:
    print("\n=== 4. OrchestratorAgent ===")

    model = LLMFactory(settings.CHAT_MODEL, settings.OPENAI_API_KEY).build()

    math_agent = ReActAgent(
        "MathSpecialist",
        model=model,
        tools=[MathTool()],
        context=ContextConfig(InMemoryHistoryProvider(), pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=20)])),
        system_instructions="You are a mathematics specialist. Use the math tool for every calculation.",
        max_iterations=5,
    )
    time_agent = ReActAgent(
        "TimeSpecialist",
        model=model,
        tools=[ClockTool()],
        context=ContextConfig(InMemoryHistoryProvider(), pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=20)])),
        system_instructions="You are a time specialist. Always check the clock tool.",
        max_iterations=4,
    )
    orchestrator = OrchestratorAgent(
        "Router",
        model=model,
        sub_agents=[
            SubAgentConfig(agent=math_agent, description="Handles mathematical calculations."),
            SubAgentConfig(agent=time_agent, description="Reports current time information."),
        ],
        max_iterations=10,
    )

    async with Runtime() as rt:
        await rt.register(math_agent)
        await rt.register(time_agent)
        await rt.register(orchestrator)
        output = await run_agent(rt, orchestrator, "What is math.log2(1024)? Also, what is the current UTC time?")
    print(f"  output : {output!r}")


# ---------------------------------------------------------------------------
# 5. Interactive REPL with OrchestratorAgent
# ---------------------------------------------------------------------------


async def demo_interactive() -> None:
    print("\n=== 5. Interactive REPL (OrchestratorAgent) ===")
    print("Type 'exit' to quit.\n")

    model = LLMFactory(settings.CHAT_MODEL, settings.OPENAI_API_KEY).build()
    math_agent = ReActAgent(
        "MathSpecialist",
        model=model,
        tools=[MathTool()],
        context=ContextConfig(InMemoryHistoryProvider(), pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=20)])),
        system_instructions="You are a mathematics specialist. Use the math tool for every calculation.",
    )
    time_agent = ReActAgent(
        "TimeSpecialist",
        model=model,
        tools=[ClockTool()],
        context=ContextConfig(InMemoryHistoryProvider(), pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=20)])),
        system_instructions="You are a time specialist. Always check the clock tool.",
    )
    orchestrator = OrchestratorAgent(
        "Router",
        model=model,
        sub_agents=[
            SubAgentConfig(agent=math_agent, description="Handles mathematical calculations."),
            SubAgentConfig(agent=time_agent, description="Reports current time information."),
        ],
        max_iterations=10,
    )

    session = "repl-session"
    async with Runtime() as rt:
        await rt.register(math_agent)
        await rt.register(time_agent)
        await rt.register(orchestrator)
        while True:
            try:
                user_input = input("You: ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\nBye!")
                break
            if user_input.lower() in ("exit", "quit", "q"):
                print("Bye!")
                break
            if not user_input:
                continue
            output = await run_agent(rt, orchestrator, user_input, session_id=session)
            print(f"Bot: {output}\n")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def main() -> None:
    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY not set — add it to ravi-engine/.env")

    print("\n" + "=" * 50)
    print("      Ravi Agent Framework Reference Demos      ")
    print("=" * 50)
    print("Select a demo to run:")
    print("  1. Basic single-turn run")
    print("  2. Multi-turn conversation via shared history")
    print("  3. UserProxyAgent routing via Runtime")
    print("  4. OrchestratorAgent (Hub & Spoke)")
    print("  5. Interactive REPL with OrchestratorAgent")
    print("  exit. Exit")

    while True:
        try:
            choice = input("\nSelect demo [1-5, exit]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nBye!")
            break

        if choice.lower() in ("exit", "quit", "q"):
            print("Bye!")
            break
        elif choice == "1":
            await demo_basic_run()
        elif choice == "2":
            await demo_multi_turn()
        elif choice == "3":
            await demo_proxy()
        elif choice == "4":
            await demo_orchestrator()
        elif choice == "5":
            await demo_interactive()
        else:
            print("Invalid choice, select 1 to 5 or exit.")


if __name__ == "__main__":
    asyncio.run(main())
