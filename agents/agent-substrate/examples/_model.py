"""The model the examples talk to.

``pick_model(...)`` returns a real OpenAI model when ``OPENAI_API_KEY`` is set, and otherwise a scripted one that
replays the replies you give it — so every example runs end to end with no key and no network. Set
``SUBSTRATE_EXAMPLES_OFFLINE=1`` to force the scripted model even when a key is present.

``ScriptedModel`` is also the smallest possible ``ChatModel``: the engine asks a model for four things
(``model``, ``capabilities``, ``generate``/``generate_stream``, ``count_tokens``), and nothing else. Your own
provider is a class with those, passed in as ``model=`` — no registration anywhere.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from substrate.models import ChatModel, FinishReason, GenerationOptions, LLMResponse, ModelCapabilities
from substrate.types import (
    ChatMessage,
    CompletionEvent,
    ContentBlock,
    Role,
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


def last_user_text(messages: list[ChatMessage]) -> str:
    return next((m.text for m in reversed(messages) if m.role == Role.USER), "")


def offline() -> bool:
    return bool(os.environ.get("SUBSTRATE_EXAMPLES_OFFLINE")) or not os.environ.get("OPENAI_API_KEY")


def pick_model(*script: Reply, model: str = "gpt-5.4-mini") -> ChatModel:
    """A real model when a key is set, else a ``ScriptedModel`` that replays ``script``."""
    if offline():
        return ScriptedModel(*script)
    from substrate.integrations.llm import LLMFactory

    return LLMFactory(model, os.environ["OPENAI_API_KEY"]).build()
