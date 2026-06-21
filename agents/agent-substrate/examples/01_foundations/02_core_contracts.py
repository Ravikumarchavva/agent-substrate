"""Example 1-2: Core Contracts
Module: substrate.kernel, substrate.kernel.stream, substrate.kernel.content

Demonstrates the new kernel data model — all offline, no external services
required. Covers: ChatMessage, ContentBlock subtypes, Tool Protocol, stream
event types, and how the ReAct history loop is shaped.

Run:
    cd agent-substrate
    uv run examples/01_foundations/02_core_contracts.py
"""

from __future__ import annotations

import asyncio

from substrate.kernel import (
    AgentId,
    ChatMessage,
    TextBlock,
    ToolExecutionResult,
    ToolResultBlock,
    ToolUseBlock,
)
from substrate.kernel.stream import CompletionEvent, ReasoningDelta, StreamDone, TextDelta


# ---------------------------------------------------------------------------
# 1. ChatMessage — the conversation currency
# ---------------------------------------------------------------------------

async def demo_chat_message() -> None:
    print("=== 1. ChatMessage ===")

    user_msg = ChatMessage(
        role="user",
        content=[TextBlock(text="What is 2 + 2?")],
    )
    assistant_msg = ChatMessage(
        role="assistant",
        content=[
            TextBlock(text="I'll use the calculator."),
            ToolUseBlock(call_id="tc-001", tool_name="calculator", arguments={"expression": "2+2"}),
        ],
    )
    tool_msg = ChatMessage(
        role="user",
        content=[
            ToolResultBlock(
                call_id="tc-001",
                content=[TextBlock(text="4")],
                is_error=False,
            )
        ],
    )
    final_msg = ChatMessage(
        role="assistant",
        content=[TextBlock(text="The answer is 4.")],
    )

    for msg in [user_msg, assistant_msg, tool_msg, final_msg]:
        texts = [b.text for b in msg.content if isinstance(b, TextBlock)]
        tools = [b.tool_name for b in msg.content if isinstance(b, ToolUseBlock)]
        results = [b.call_id for b in msg.content if isinstance(b, ToolResultBlock)]
        print(f"  role={msg.role:<10} texts={texts} tools={tools} results={results}")


# ---------------------------------------------------------------------------
# 2. ContentBlock subtypes
# ---------------------------------------------------------------------------

async def demo_content_blocks() -> None:
    print("\n=== 2. ContentBlock subtypes ===")

    text = TextBlock(text="Hello, world!")
    tool_call = ToolUseBlock(
        call_id="abc-123",
        tool_name="search",
        arguments={"query": "agent frameworks"},
    )
    tool_result = ToolResultBlock(
        call_id="abc-123",
        content=[TextBlock(text="agent-substrate is a Python async AI-agent framework")],
        is_error=False,
    )
    error_result = ToolResultBlock(
        call_id="abc-123",
        content=[TextBlock(text="Connection refused")],
        is_error=True,
    )

    print(f"  TextBlock           : {text.to_text_repr()!r}")
    print(f"  ToolUseBlock        : {tool_call.to_text_repr()!r}")
    print(f"  ToolResultBlock     : {tool_result.to_text_repr()!r}")
    print(f"  ToolResultBlock err : {error_result.to_text_repr()!r}")


# ---------------------------------------------------------------------------
# 3. Tool Protocol — structural satisfaction (no base class)
# ---------------------------------------------------------------------------

async def demo_tool_protocol() -> None:
    print("\n=== 3. Tool Protocol (structural) ===")

    class EchoTool:
        name = "echo"
        description = "Returns the input string unchanged."
        input_schema: dict[str, object] = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }

        async def execute(self, *, text: str, **_kw: object) -> ToolExecutionResult:
            return ToolExecutionResult(
                name=self.name,
                content=[TextBlock(text=text)],
            )

    tool = EchoTool()
    result = await tool.execute(text="hello kernel")

    print(f"  tool.name        : {tool.name!r}")
    print(f"  tool.description : {tool.description!r}")
    output_text = result.content[0].text if result.content else ""
    print(f"  execute result   : {output_text!r}")
    print(f"  is_error         : {result.is_error}")


# ---------------------------------------------------------------------------
# 4. Stream event types
# ---------------------------------------------------------------------------

async def demo_stream_events() -> None:
    print("\n=== 4. Stream event types ===")

    # Simulate what an agent emits — consumed via rt.submit() + rt.event_log.tail()
    simulated_stream = [
        TextDelta(text="The "),
        TextDelta(text="answer "),
        ReasoningDelta(text="<thinking>compute 2+2</thinking>"),
        TextDelta(text="is 4."),
        CompletionEvent(content=[TextBlock(text="The answer is 4.")]),
        StreamDone(reason="complete"),
    ]

    assembled_text: list[str] = []
    for event in simulated_stream:
        if isinstance(event, TextDelta):
            assembled_text.append(event.text)
            print(f"  TextDelta       : {event.text!r}")
        elif isinstance(event, ReasoningDelta):
            print(f"  ReasoningDelta  : {event.text!r}")
        elif isinstance(event, CompletionEvent):
            texts = [b.text for b in event.content if isinstance(b, TextBlock)]
            print(f"  CompletionEvent : content={texts}")
        elif isinstance(event, StreamDone):
            print(f"  StreamDone      : reason={event.reason!r}")
            break

    print(f"  assembled        : {''.join(assembled_text)!r}")


# ---------------------------------------------------------------------------
# 5. AgentId — routing identity
# ---------------------------------------------------------------------------

async def demo_agent_id() -> None:
    print("\n=== 5. AgentId ===")

    assistant = AgentId(type="assistant", key="session-abc")
    proxy = AgentId(type="proxy", key="default")
    random_id = AgentId.generate("orchestrator")

    print(f"  assistant : {assistant!r}  str={str(assistant)!r}")
    print(f"  proxy     : {proxy!r}")
    print(f"  generated : {random_id!r}  (random key)")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

async def main() -> None:
    await demo_chat_message()
    await demo_content_blocks()
    await demo_tool_protocol()
    await demo_stream_events()
    await demo_agent_id()
    print("\nAll contract demos passed.")


if __name__ == "__main__":
    asyncio.run(main())
