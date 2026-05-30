"""Example 1-3: ReAct Agent — Full Reference
Module: ravi.reasoning.agents.assistant, ravi.orchestration.agents

Complete reference showing the new actor-model wiring:
  - LocalRuntime with explicit handler registration
  - AssistantAgent + UserProxyAgent pattern
  - Streaming with per-event callbacks
  - Multi-turn conversation via InMemoryHistoryProvider
  - OrchestratorAgent delegating to two specialist agents

Run:
    cd ravi-engine
    OPENAI_API_KEY=sk-... uv run examples/01_foundations/03_react_agent_reference.py
"""

from __future__ import annotations

import asyncio
import datetime
import math

from ravi.configs.settings import settings
from ravi.fabric.context import InMemoryHistoryProvider, SlidingWindowCompaction
from ravi.fabric.runtime.local import LocalRuntime
from ravi.integrations.llm.openai.openai_client import OpenAIClient
from ravi.kernel import TextBlock, ToolExecutionResult
from ravi.kernel.stream import CompletionEvent, StreamDone, TextDelta
from ravi.orchestration.agents.orchestrator.agent import OrchestratorAgent
from ravi.orchestration.agents.proxy.agent import UserProxyAgent
from ravi.reasoning.agents.assistant.agent import AgentRunResult, AssistantAgent


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


class MathTool:
    name = "math"
    description = (
        "Evaluate a Python math expression. "
        "You may use any function from the `math` module."
    )
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "e.g. 'math.sqrt(144)'"}
        },
        "required": ["expression"],
    }

    async def execute(self, *, expression: str, **_kw: object) -> ToolExecutionResult:
        try:
            result = eval(expression, {"math": math, "__builtins__": {}})  # noqa: S307
            return ToolExecutionResult(name=self.name, content=[TextBlock(text=str(result))])
        except Exception as exc:
            return ToolExecutionResult(
                name=self.name, content=[TextBlock(text=str(exc))], is_error=True
            )


class ClockTool:
    name = "clock"
    description = "Return the current UTC timestamp."
    input_schema: dict[str, object] = {"type": "object", "properties": {}}

    async def execute(self, **_kw: object) -> ToolExecutionResult:
        ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return ToolExecutionResult(name=self.name, content=[TextBlock(text=ts)])


# ---------------------------------------------------------------------------
# 1. Basic run() — non-streaming, single turn
# ---------------------------------------------------------------------------


async def demo_basic_run(rt: LocalRuntime) -> None:
    print("=== 1. Basic run() ===")

    model = OpenAIClient(model=settings.CHAT_MODEL.split("/")[-1])
    agent = AssistantAgent(
        "Calculator",
        rt,
        model=model,
        tools=[MathTool()],
        system="You are a maths assistant. Use the math tool for any calculation.",
        history=InMemoryHistoryProvider(),
        compaction=SlidingWindowCompaction(max_messages=20),
        max_iterations=6,
    )

    result: AgentRunResult = await agent.run("What is math.sqrt(256) * math.pi?")
    print(f"  status : {result.status}")
    print(f"  output : {result.output!r}")
    for tc in result.tool_calls:
        print(f"  tool   : {tc.name}({tc.arguments})  → {tc.result!r}")


# ---------------------------------------------------------------------------
# 2. run_stream() — streaming with per-event callbacks
# ---------------------------------------------------------------------------


async def demo_streaming(rt: LocalRuntime) -> None:
    print("\n=== 2. run_stream() ===")

    model = OpenAIClient(model=settings.CHAT_MODEL.split("/")[-1])
    agent = AssistantAgent(
        "Streamer",
        rt,
        model=model,
        tools=[ClockTool()],
        history=InMemoryHistoryProvider(),
        compaction=SlidingWindowCompaction(max_messages=20),
        max_iterations=5,
    )

    print("  stream: ", end="", flush=True)
    async for event in agent.run_stream("What is the current UTC time?"):
        if isinstance(event, TextDelta):
            print(event.text, end="", flush=True)
        elif isinstance(event, CompletionEvent):
            pass  # final assembled content available here if needed
        elif isinstance(event, StreamDone):
            break
    print()  # newline after streamed output


# ---------------------------------------------------------------------------
# 3. Multi-turn conversation via shared HistoryProvider
# ---------------------------------------------------------------------------


async def demo_multi_turn(rt: LocalRuntime) -> None:
    print("\n=== 3. Multi-turn conversation ===")

    model = OpenAIClient(model=settings.CHAT_MODEL.split("/")[-1])
    history = InMemoryHistoryProvider()
    agent = AssistantAgent(
        "Tutor",
        rt,
        model=model,
        tools=[MathTool()],
        history=history,
        compaction=SlidingWindowCompaction(max_messages=40),
        max_iterations=6,
    )

    turns = [
        "My name is Ada. Remember that.",
        "What is 7 * 8?",
        "What is my name and what was the result I asked for?",
    ]
    for q in turns:
        result = await agent.run(q)
        print(f"  Q: {q!r}")
        print(f"  A: {result.output!r}")

    msg_count = await history.count_messages(agent.id.key)
    print(f"  history depth: {msg_count} messages")


# ---------------------------------------------------------------------------
# 4. UserProxyAgent → AssistantAgent via LocalRuntime
# ---------------------------------------------------------------------------


async def demo_proxy(rt: LocalRuntime) -> None:
    print("\n=== 4. UserProxyAgent → AssistantAgent ===")

    model = OpenAIClient(model=settings.CHAT_MODEL.split("/")[-1])
    agent = AssistantAgent(
        "Backend",
        rt,
        model=model,
        tools=[MathTool(), ClockTool()],
        history=InMemoryHistoryProvider(),
        compaction=SlidingWindowCompaction(max_messages=20),
        max_iterations=6,
    )
    await rt.register(agent.id.type, agent.on_message)

    proxy = UserProxyAgent("proxy", rt, key="user-1")
    result = await proxy.ask(
        "What is math.factorial(10)?",
        recipient=agent.id,
    )
    print(f"  proxy.ask result : {getattr(result, 'output', result)!r}")


# ---------------------------------------------------------------------------
# 5. OrchestratorAgent — delegates to specialist sub-agents
# ---------------------------------------------------------------------------


async def demo_orchestrator(rt: LocalRuntime) -> None:
    print("\n=== 5. OrchestratorAgent ===")

    model = OpenAIClient(model=settings.CHAT_MODEL.split("/")[-1])

    math_agent = AssistantAgent(
        "MathSpecialist",
        rt,
        model=model,
        tools=[MathTool()],
        system="You are a mathematics specialist. Use the math tool for every calculation.",
        history=InMemoryHistoryProvider(),
        compaction=SlidingWindowCompaction(max_messages=20),
        max_iterations=5,
    )
    math_agent.description = "Handles mathematical calculations and expressions."

    time_agent = AssistantAgent(
        "TimeSpecialist",
        rt,
        model=model,
        tools=[ClockTool()],
        system="You are a time specialist. Always check the clock tool.",
        history=InMemoryHistoryProvider(),
        compaction=SlidingWindowCompaction(max_messages=20),
        max_iterations=4,
    )
    time_agent.description = "Reports current time and timezone information."

    orchestrator = OrchestratorAgent(
        "Router",
        rt,
        model=model,
        sub_agents=[math_agent, time_agent],
        description="Routes queries to math or time specialists.",
        max_iterations=10,
    )

    result = await orchestrator.run(
        "What is math.log2(1024)? Also, what is the current UTC time?"
    )
    print(f"  status : {result.status}")
    print(f"  output : {result.output!r}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def main() -> None:
    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY not set — add it to ravi-engine/.env")

    async with LocalRuntime() as rt:
        await demo_basic_run(rt)
        await demo_streaming(rt)
        await demo_multi_turn(rt)
        await demo_proxy(rt)
        await demo_orchestrator(rt)

    print("\nAll reference demos complete.")


if __name__ == "__main__":
    asyncio.run(main())
