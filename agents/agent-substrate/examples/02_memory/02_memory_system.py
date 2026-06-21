"""Example 2-2: History Compaction — sliding window and token-based history compression.

Demonstrates:
  • SlidingWindowCompaction: drop old turns beyond N messages.
  • SummarizationCompaction: condense older turns using an LLM while keeping recent turns verbatim.
"""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from substrate.agents.context import SlidingWindowCompaction, SummarizationCompaction
from substrate.kernel.core.content import ChatMessage, Role, TextBlock
from substrate.kernel.llm import GenerationOptions, LLMResponse, Usage
from substrate.kernel.messaging.stream import CompletionEvent, TextDelta


class MockLLM:
    """Mock LLM to simulate summarization without an API key."""
    def __init__(self, reply: str) -> None:
        self.reply = reply

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),
    ) -> LLMResponse:
        return LLMResponse(content=[TextBlock(text=self.reply)], usage=Usage())


async def main() -> None:
    # Build a simulated conversation history (12 turns / messages)
    history: list[ChatMessage] = []
    for i in range(1, 7):
        history.append(ChatMessage(role=Role.USER, content=[TextBlock(text=f"User message {i}")]))
        history.append(ChatMessage(role=Role.ASSISTANT, content=[TextBlock(text=f"Assistant response {i}")]))

    print("=== Original History ===")
    print(f"Total messages: {len(history)}")
    for i, msg in enumerate(history):
        print(f"  {i+1:02d}. [{msg.role}]: {msg.content[0].text}")

    # 1. Sliding Window Compaction (limit to 6 messages)
    print("\n=== 1. SlidingWindowCompaction ===")
    sliding = SlidingWindowCompaction(max_messages=6)
    compacted_sliding = await sliding.compact(history)
    print(f"Compacted messages: {len(compacted_sliding)}")
    for i, msg in enumerate(compacted_sliding):
        print(f"  {i+1:02d}. [{msg.role}]: {msg.content[0].text}")

    # 2. Summarization Compaction (condense older messages, keep recent budget verbatim)
    print("\n=== 2. SummarizationCompaction ===")
    mock_model = MockLLM("The user and assistant had a brief introductory conversation.")
    # Set a tiny budget to force compaction of old messages
    summarizer = SummarizationCompaction(
        model=mock_model,  # type: ignore[arg-type]
        recent_token_budget=50,
        min_old_tokens=1,
        chars_per_token=1.0,
    )
    compacted_sum = await summarizer.compact(history)
    print(f"Compacted messages: {len(compacted_sum)}")
    for i, msg in enumerate(compacted_sum):
        print(f"  {i+1:02d}. [{msg.role}]: {msg.content[0].text}")


if __name__ == "__main__":
    asyncio.run(main())
