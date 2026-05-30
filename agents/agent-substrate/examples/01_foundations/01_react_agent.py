"""Example 1-1: ReAct Agent
Module: ravi.reasoning.agents.assistant, ravi.fabric.runtime.local

Demonstrates the ReAct (Reason + Act) loop with two inline tools running on
LocalRuntime. The agent receives a user message, decides which tool(s) to call,
executes them, and synthesises a final text response.

Run:
    cd ravi-engine
    uv run examples/01_foundations/01_react_agent.py
"""

from __future__ import annotations

import asyncio
import datetime

from ravi.configs.settings import settings
from ravi.fabric.context import AgentContext, InMemoryHistoryProvider, SlidingWindowCompaction
from ravi.fabric.runtime.local import LocalRuntime
from ravi.integrations.llm.openai.openai_client import OpenAIClient
from ravi.kernel import TextBlock, ToolExecutionResult
from ravi.reasoning.agents.assistant.agent import AssistantAgent


# ---------------------------------------------------------------------------
# Inline tools — satisfy the Tool Protocol structurally (no base class needed)
# ---------------------------------------------------------------------------


class CalculatorTool:
    name = "calculator"
    description = "Evaluate a simple arithmetic expression and return the result."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "A Python arithmetic expression, e.g. '2 ** 10 + 4'.",
            }
        },
        "required": ["expression"],
    }

    async def execute(self, *, expression: str, **_kw: object) -> ToolExecutionResult:
        try:
            result = eval(expression, {"__builtins__": {}})  # noqa: S307
            return ToolExecutionResult(
                name=self.name,
                content=[TextBlock(text=str(result))],
            )
        except Exception as exc:
            return ToolExecutionResult(
                name=self.name,
                content=[TextBlock(text=f"Error: {exc}")],
                is_error=True,
            )


class GetCurrentTimeTool:
    name = "get_current_time"
    description = "Return the current UTC date and time as an ISO-8601 string."
    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {},
    }

    async def execute(self, **_kw: object) -> ToolExecutionResult:
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        return ToolExecutionResult(
            name=self.name,
            content=[TextBlock(text=now)],
        )


# ---------------------------------------------------------------------------
# Agent setup
# ---------------------------------------------------------------------------


def build_agent(runtime: LocalRuntime) -> AssistantAgent:
    model = OpenAIClient(model=settings.CHAT_MODEL.split("/")[-1], api_key=settings.OPENAI_API_KEY)
    context = AgentContext(
        InMemoryHistoryProvider(),
        [SlidingWindowCompaction(max_messages=40)],
    )
    return AssistantAgent(
        "DemoBot",
        runtime,
        model=model,
        tools=[CalculatorTool(), GetCurrentTimeTool()],
        context=context,
        max_iterations=8,
    )


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------


async def main() -> None:
    if not settings.OPENAI_API_KEY:
        raise SystemExit("OPENAI_API_KEY not set — add it to ravi-engine/.env")

    async with LocalRuntime() as rt:
        agent = build_agent(rt)

        questions = [
            "What is 2 ** 10 + 7?",
            "What is the current UTC time?",
            "What is (123 * 456) / 789 rounded to 4 decimal places?",
        ]

        for q in questions:
            print(f"\nUser: {q}")
            result = await agent.run(q)
            print(f"Agent [{result.status}]: {result.output}")
            if result.tool_calls:
                for tc in result.tool_calls:
                    print(f"  tool={tc.name}  result={tc.result!r}")


if __name__ == "__main__":
    asyncio.run(main())
