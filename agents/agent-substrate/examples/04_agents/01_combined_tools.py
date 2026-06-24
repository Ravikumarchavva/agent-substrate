"""04-1 — Combined Built-in Tools + Custom Tool Class

Demonstrates building a ReActAgent with multiple built-in tools and a custom
inline tool defined as a class.

Prerequisites: OPENAI_API_KEY set.
"""

from __future__ import annotations

from dotenv import load_dotenv
from substrate.config import SubstrateConfig

load_dotenv()  # walks up to find the repo-root .env
settings = SubstrateConfig()


import asyncio
import uuid
from substrate.agents import ReActAgent, Runtime
from substrate.agents.context import (
    ContextConfig,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
    CompactionPipeline,
)
from substrate.integrations.llm import (
    create_model_client,
    detect_provider,
    has_provider_api_key,
)
from substrate.capabilities.tools import CalculatorTool, CurrentTimeTool
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import Message, ChatPayload
from substrate.kernel.tools import ToolExecutionResult


async def run_agent(
    rt: Runtime, agent: ReActAgent, text: str, *, session_id: str
) -> str:
    msg = Message(
        target=agent.id,
        sender=AgentId(type="proxy", key="user"),
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=text)])
        ),
        correlation_id=session_id,
    )
    run_id = await rt.submit(agent.id, msg)
    async for entry in rt.event_log.tail(run_id):
        if entry.kind in ("run.completed", "run.failed", "run.cancelled"):
            break
    history = await agent.history.get_messages(agent.id, session_id=session_id)
    for m in reversed(history):
        if m.role == Role.ASSISTANT:
            return " ".join(
                b.text for b in m.content if isinstance(b, TextBlock) and b.text
            )
    return ""


async def main() -> None:
    provider = detect_provider(settings.CHAT_MODEL)
    if not has_provider_api_key(provider, settings.provider_keys):
        raise SystemExit(
            f"No API key for provider {provider!r} (model {settings.CHAT_MODEL!r}). "
            f"Add the matching key to agent-substrate/.env."
        )

    model = create_model_client(settings.CHAT_MODEL, api_keys=settings.provider_keys)
    session_id = uuid.uuid4().hex

    # ---
    # Section 1: Agent with CalculatorTool + CurrentTimeTool
    agent = ReActAgent(
        "DemoBot",
        model=model,
        tools=[CalculatorTool(), CurrentTimeTool()],
        context=ContextConfig(
            InMemoryHistoryProvider(),
            pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=40)]),
        ),
        max_iterations=5,
    )

    async with Runtime() as rt:
        await rt.register(agent)

        # ---
        # Section 2: Single task that uses both tools in one query
        output = await run_agent(
            rt,
            agent,
            "What is 1337 * 42? Also tell me the current UTC time.",
            session_id=session_id,
        )
        print("=== Section 2: Combined tool call ===")
        print(output)

        # ---
        # Section 3: Multi-turn conversation — memory is preserved across run() calls
        print("\n=== Section 3: Multi-turn (context retained) ===")
        r1 = await run_agent(
            rt, agent, "My lucky number is 7. Remember it.", session_id=session_id
        )
        print("Turn 1:", r1)

        r2 = await run_agent(
            rt, agent, "What is my lucky number multiplied by 6?", session_id=session_id
        )
        print("Turn 2:", r2)

        # Reset agent history to start a fresh session
        await agent.history.clear_session(agent.id, session_id=session_id)
        r3 = await run_agent(
            rt, agent, "What is my lucky number?", session_id=session_id
        )
        print("Turn 3 (after clear — should not remember):", r3)

        # ---
        # Section 4: Custom tool class satisfying structural tool protocol
        class CelsiusToFahrenheitTool:
            name = "celsius_to_fahrenheit"
            description = "Convert a temperature from Celsius to Fahrenheit."
            input_schema: dict[str, object] = {
                "type": "object",
                "properties": {
                    "celsius": {
                        "type": "number",
                        "description": "Temperature in Celsius",
                    }
                },
                "required": ["celsius"],
            }

            async def execute(self, *, celsius: float, **kwargs) -> ToolExecutionResult:
                fahrenheit = celsius * 9 / 5 + 32
                return ToolExecutionResult(
                    name=self.name,
                    content=[TextBlock(text=f"{celsius}°C = {fahrenheit}°F")],
                )

        agent2 = ReActAgent(
            "ConverterBot",
            model=model,
            tools=[CelsiusToFahrenheitTool()],
            context=ContextConfig(
                InMemoryHistoryProvider(),
                pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=40)]),
            ),
            max_iterations=5,
        )
        await rt.register(agent2)

        print("\n=== Section 4: Custom Tool Class ===")
        result2 = await run_agent(
            rt, agent2, "Convert 100°C and -40°C to Fahrenheit.", session_id=session_id
        )
        print(result2)


if __name__ == "__main__":
    asyncio.run(main())
