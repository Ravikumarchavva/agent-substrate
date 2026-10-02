"""A ``ChatModel`` of our own — here a tiny rule-based one, so the project runs with no API key.

The engine asks a model for four things: ``model``, ``capabilities``, ``generate`` / ``generate_stream`` and
``count_tokens``. A real provider is a class with the same four, calling its SDK inside.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from substrate.models import FinishReason, GenerationOptions, LLMResponse, ModelCapabilities
from substrate.types import (
    ChatMessage,
    CompletionEvent,
    ContentBlock,
    Role,
    TextBlock,
    TextDelta,
    ToolResultBlock,
    ToolUseBlock,
    Usage,
)


class DeskModel:
    """Looks up the ticket the user names; closes it if asked; then reports what the tools said."""

    model = "desk-rules"
    capabilities = ModelCapabilities(model_id="desk-rules")

    def _decide(self, messages: list[ChatMessage]) -> list[ContentBlock]:
        last = messages[-1]
        results = [b for b in last.content if isinstance(b, ToolResultBlock)]
        if results:  # the tools have answered: say what happened
            return [TextBlock(text=" ".join(r.text for r in results))]
        user = next(m.text for m in reversed(messages) if m.role == Role.USER)
        ticket = re.search(r"T-\d+", user)
        if ticket and re.search(r"\bclose\b", user, re.I):
            return [ToolUseBlock(call_id="c1", tool_name="close_ticket", arguments={"ticket_id": ticket.group(), "resolution": "fixed"})]
        if ticket:
            return [ToolUseBlock(call_id="c1", tool_name="lookup_ticket", arguments={"ticket_id": ticket.group()})]
        return [TextBlock(text="Which ticket?")]

    async def generate(self, messages: list[ChatMessage], *, options: GenerationOptions = GenerationOptions(), ctx: Any = None) -> LLMResponse:
        return LLMResponse(content=self._decide(messages), usage=Usage(), finish_reason=FinishReason.STOP)

    async def generate_stream(self, messages: list[ChatMessage], *, options: GenerationOptions = GenerationOptions(), ctx: Any = None) -> AsyncIterator[Any]:
        content = self._decide(messages)
        for block in content:
            if isinstance(block, TextBlock):
                yield TextDelta(text=block.text)
        yield CompletionEvent(content=content, usage=Usage(), finish_reason=FinishReason.STOP)

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return sum(len(m.text) for m in messages) // 4
