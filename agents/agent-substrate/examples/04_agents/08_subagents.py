"""Example 4-8: Orchestrator + Sub-agents

Demonstrates:
  • OrchestratorAgent routing tasks to specialist ReActAgents
  • SubAgentConfig — per-agent priority (HIGH / NORMAL)
  • Runtime message-passing for crash-isolated delegation
  • StubLLMClient — run this example with no API key at all

Run:
    cd agent-substrate
    uv run examples/04_agents/08_subagents.py            # no API key needed
    OPENAI_API_KEY=sk-... uv run examples/04_agents/08_subagents.py  # real LLM
"""

from __future__ import annotations

from dotenv import load_dotenv
from substrate.config import SubstrateConfig

load_dotenv()  # walks up to find the repo-root .env
settings = SubstrateConfig()


import asyncio
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator

from substrate.agents.context import (
    ContextConfig,
    InMemoryHistoryProvider,
    SlidingWindowCompaction,
    CompactionPipeline,
)
from substrate.agents.core.react import ReActAgent
from substrate.agents.core.orchestrator import OrchestratorAgent, SubAgentConfig
from substrate.agents.runtime import Runtime
from substrate.agents.middleware import AgentRunResult
from substrate.kernel.core.content import Role
from substrate.kernel.messaging.message import Message, ChatPayload
from substrate.capabilities.tools import CalculatorTool, CurrentTimeTool, WebSearchTool
from substrate.kernel import (
    Priority,
    TextBlock,
    Tool,
    ToolUseBlock,
    ChatMessage,
    ContentBlock,
    CompletionEvent,
    ReasoningDelta,
    TextDelta,
    AgentId,
)


# ---------------------------------------------------------------------------
# StubLLMClient — scripted responses, no API key needed
# ---------------------------------------------------------------------------


@dataclass
class StubLLMClient:
    """Scripted LLM stub for demo purposes.

    Orchestrator always delegates to the researcher, then the writer.
    Specialists echo their input back as a polished response.
    """

    _calls: int = field(default=0, init=False)
    _agent_calls: dict[str, int] = field(default_factory=dict, init=False)

    def _agent_name(self, system: str) -> str:
        for keyword in ("researcher", "writer"):
            if keyword in system.lower():
                return keyword
        return "orchestrator"

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[Tool] | None = None,
        system: str = "",
        **_: object,
    ) -> list[ContentBlock]:
        name = self._agent_name(system)
        call = self._agent_calls.get(name, 0)
        self._agent_calls[name] = call + 1

        last_user = next(
            (
                m.content[-1].text
                for m in reversed(messages)
                if m.role == "user"  # type: ignore[union-attr]
                and m.content
                and hasattr(m.content[-1], "text")
            ),
            "unknown task",
        )

        # tools are schema dicts: {"name": ..., "description": ..., "parameters": ...}
        tool_names = {
            (t["name"] if isinstance(t, dict) else t.name) for t in (tools or [])
        }
        dispatch_names = tool_names
        has_researcher = any("researcher" in n for n in dispatch_names)
        has_writer = any("writer" in n for n in dispatch_names)

        # Orchestrator: first call delegates to researcher, second to writer, third synthesises
        if has_researcher or has_writer:
            if call == 0 and has_researcher:
                return [
                    ToolUseBlock(
                        call_id=uuid.uuid4().hex[:8],
                        tool_name=next(n for n in dispatch_names if "researcher" in n),
                        arguments={
                            "input": last_user,
                            "reason": "Need factual research first",
                        },
                    )
                ]
            if call == 1 and has_writer:
                last_tool_result = next(
                    (
                        block.content[0].text  # type: ignore[union-attr]
                        for m in reversed(messages)
                        if m.role == "tool"
                        for block in m.content
                        if hasattr(block, "content") and block.content
                    ),
                    last_user,
                )
                return [
                    ToolUseBlock(
                        call_id=uuid.uuid4().hex[:8],
                        tool_name=next(n for n in dispatch_names if "writer" in n),
                        arguments={
                            "input": f"Write a summary of: {last_tool_result[:200]}",
                            "reason": "Format research for the user",
                        },
                    )
                ]
            # Final synthesis
            return [
                TextBlock(
                    text=f"[Orchestrator] Task complete. Both specialists have responded to: '{last_user[:80]}'"
                )
            ]

        # Specialist agents
        if name == "researcher":
            return [
                TextBlock(
                    text=f"[Researcher] Found relevant information about: {last_user[:120]}. Key facts: Python was created by Guido van Rossum, released in 1991, widely used in AI/ML, data science, and web development."
                )
            ]
        if name == "writer":
            return [TextBlock(text=f"[Writer] Summary: {last_user[:120]}")]
        return [TextBlock(text=f"[{name}] Processed: {last_user[:80]}")]

    async def generate_stream(
        self,
        messages: list[ChatMessage],
        **kwargs: object,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]:
        content = await self.generate(messages, **kwargs)  # type: ignore[arg-type]

        async def _gen() -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]:
            yield CompletionEvent(content=content)

        return _gen()

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return sum(len(str(m)) for m in messages) // 4


# ---------------------------------------------------------------------------
# Agent factory helpers
# ---------------------------------------------------------------------------


def _context() -> ContextConfig:
    return ContextConfig(
        InMemoryHistoryProvider(),
        pipeline=CompactionPipeline([SlidingWindowCompaction(max_messages=20)]),
    )


def build_agents(runtime: Runtime, model: object) -> OrchestratorAgent:
    """Build orchestrator + two specialist sub-agents."""

    researcher = ReActAgent(
        "researcher",
        model=model,  # type: ignore[arg-type]
        system_instructions="You are a researcher agent. Search the web and return factual information.",
        tools=[WebSearchTool()],
        context=_context(),
        max_iterations=4,
    )

    writer = ReActAgent(
        "writer",
        model=model,  # type: ignore[arg-type]
        system_instructions="You are a writer agent. Format and write clear, concise summaries.",
        tools=[CurrentTimeTool()],
        context=_context(),
        max_iterations=4,
    )

    calculator = ReActAgent(
        "calculator",
        model=model,  # type: ignore[arg-type]
        system_instructions="You are a calculator agent. Perform precise numerical calculations.",
        tools=[CalculatorTool()],
        context=_context(),
        max_iterations=4,
    )

    return OrchestratorAgent(
        "router",
        model=model,  # type: ignore[arg-type]
        sub_agents=[
            SubAgentConfig(
                researcher,
                description="Searches the web and returns factual information.",
                priority=Priority.HIGH,
            ),
            SubAgentConfig(
                writer,
                description="Formats and writes clear, concise summaries.",
                priority=Priority.NORMAL,
            ),
            SubAgentConfig(
                calculator,
                description="Performs precise numerical calculations.",
                priority=Priority.NORMAL,
            ),
        ],
        max_iterations=10,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def run_demo(
    runtime: Runtime,
    orchestrator: OrchestratorAgent,
    query: str,
    stub: StubLLMClient | None = None,
) -> AgentRunResult:
    """Run the orchestrator on a single query, printing the handoff trace."""
    if stub is not None:
        stub._agent_calls.clear()  # reset per-run so each demo starts fresh
    print(f"\n{'─' * 60}")
    print(f"Query: {query}")
    print(f"{'─' * 60}")

    await runtime.register(orchestrator)
    # Register the sub-agents as well
    for sub_cfg in orchestrator._sub_agents:
        await runtime.register(sub_cfg.agent)

    session_id = f"demo-{uuid.uuid4().hex[:8]}"
    msg = Message(
        target=orchestrator.id,
        sender=AgentId(type="proxy", key="user"),
        payload=ChatPayload(
            message=ChatMessage(role=Role.USER, content=[TextBlock(text=query)])
        ),
        correlation_id=session_id,
    )
    run_id = await runtime.submit(orchestrator.id, msg)

    status = "success"
    async for entry in runtime.event_log.tail(run_id):
        if entry.kind == "run.completed":
            break
        elif entry.kind == "run.failed":
            status = "error"
            break
        elif entry.kind == "run.cancelled":
            status = "cancelled"
            break

    # Extract output and tool calls from history
    history = await orchestrator.history.get_messages(
        orchestrator.id, session_id=session_id
    )
    output = ""
    tool_calls = []

    for m in history:
        if m.role == Role.ASSISTANT:
            # Check for text output
            text_blocks = [
                b.text for b in m.content if isinstance(b, TextBlock) and b.text
            ]
            if text_blocks:
                output = "\n".join(text_blocks)

            # Check for tool calls
            for block in m.content:
                if isinstance(block, ToolUseBlock):
                    # Find matching result in subsequent messages
                    result_text = ""
                    is_error = False
                    for next_m in history:
                        if next_m.role == Role.TOOL:
                            for res_block in next_m.content:
                                if getattr(res_block, "call_id", None) == block.call_id:
                                    result_text = getattr(res_block, "output", "")
                                    is_error = getattr(res_block, "is_error", False)
                                    break

                    from substrate.agents.middleware._contracts import ToolCallRecord

                    tool_calls.append(
                        ToolCallRecord(
                            name=block.tool_name,
                            call_id=block.call_id,
                            arguments=block.arguments,
                            result=result_text,
                            is_error=is_error,
                            duration_ms=0.0,
                        )
                    )

    result = AgentRunResult(
        output=output,
        status=status,
        tool_calls=tool_calls,
        run_id=run_id,
    )

    print(f"\nOutput:\n{result.output}")
    print(f"\nStatus: {result.status}")
    for tc in result.tool_calls:
        arrow = "✖" if tc.is_error else "✔"
        print(
            f"  {arrow} {tc.name}({tc.arguments.get('input', tc.arguments.get('text', ''))})"
        )
    return result


async def main() -> None:
    import os

    # Use real LLM if key is present; otherwise fall back to the stub
    if settings.OPENAI_API_KEY:
        from substrate.integrations.llm.openai.openai_client import OpenAIClient

        model: object = OpenAIClient(
            model=settings.CHAT_MODEL.split("/")[-1],
            api_key=settings.OPENAI_API_KEY,
        )
        print("Using OpenAI LLM")
    else:
        model = StubLLMClient()
        print(
            "No OPENAI_API_KEY found — running with StubLLMClient (scripted responses)"
        )

    async with Runtime() as rt:
        orchestrator = build_agents(rt, model)

        stub = model if isinstance(model, StubLLMClient) else None

        # Demo 1 — research + writing task (orchestrator delegates to researcher → writer)
        await run_demo(
            rt,
            orchestrator,
            "Tell me about the Python programming language and write a short summary.",
            stub,
        )

        # Demo 2 — calculation task (orchestrator delegates to researcher first in stub mode)
        await run_demo(
            rt,
            orchestrator,
            "What is 1337 * 42 + the square root of 256?",
            stub,
        )


if __name__ == "__main__":
    asyncio.run(main())
