"""A scripted ``ChatModel`` for tests and offline demos: replays the replies you give it, in order.

The engine asks a model for four things — ``model``, ``capabilities``, ``generate`` / ``generate_stream`` and
``count_tokens`` — and this is the smallest class that provides them, so it doubles as the template for your own
provider. A reply is text, a ``ToolCall`` (the model asks for a tool), or a function of the messages it was shown::

    model = ScriptedModel(ToolCall("lookup", {"id": "T-1"}), "Ticket T-1 is open.")
    agent = ReActAgent("desk", model=model, tools=[lookup])
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from substrate.models import FinishReason, GenerationOptions, LLMResponse, ModelCapabilities
from substrate.types import (
    ChatMessage,
    CompletionEvent,
    ContentBlock,
    TextBlock,
    TextDelta,
    ToolUseBlock,
    Usage,
)


@dataclass(frozen=True)
class ToolCall:
    """A scripted reply that asks for a tool instead of answering."""

    tool: str
    arguments: dict[str, Any]


Reply = str | ToolCall | Callable[[list[ChatMessage]], str]


class ScriptedModel:
    """A ``ChatModel`` that replays ``replies`` in order, repeating the last one when they run out.

    A reply is text, a ``ToolCall``, or a function of the messages the model was shown — which is how an example
    can prove what the engine put in front of the model (``lambda messages: f"I can see {len(messages)} messages"``).
    """

    capabilities = ModelCapabilities(model_id="scripted")

    def __init__(self, *replies: Reply) -> None:
        self.model = "scripted"
        self._replies = list(replies) or ["OK."]
        self._calls = 0
        self.seen: list[list[ChatMessage]] = []

    def _next(self, messages: list[ChatMessage]) -> list[ContentBlock]:
        self.seen.append(list(messages))
        reply = self._replies[min(self._calls, len(self._replies) - 1)]
        self._calls += 1
        if isinstance(reply, ToolCall):
            return [ToolUseBlock(call_id=f"call-{self._calls}", tool_name=reply.tool, arguments=reply.arguments)]
        return [TextBlock(text=reply(messages) if callable(reply) else reply)]

    async def generate(
        self, messages: list[ChatMessage], *, options: GenerationOptions = GenerationOptions(), ctx: Any = None
    ) -> LLMResponse:
        return LLMResponse(content=self._next(messages), usage=Usage(), finish_reason=FinishReason.STOP)

    async def generate_stream(
        self, messages: list[ChatMessage], *, options: GenerationOptions = GenerationOptions(), ctx: Any = None
    ) -> AsyncIterator[TextDelta | CompletionEvent]:
        content = self._next(messages)
        for block in content:
            if isinstance(block, TextBlock):
                yield TextDelta(text=block.text)
        yield CompletionEvent(content=content, usage=Usage(), finish_reason=FinishReason.STOP)

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return sum(len(m.text) for m in messages) // 4


__all__ = ["Reply", "ScriptedModel", "ToolCall"]
