"""LLM test doubles the register's scenarios are built from.

These are the doubles that move into ``kernel/testing`` once it exists — a
conformance suite and a crash matrix both need a model whose behaviour is
exactly known.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from substrate.types import ChatMessage, ContentBlock, TextBlock
from substrate.types import Usage
from substrate.models import GenerationOptions, ModelCapabilities
from substrate.types import CompletionEvent, TextDelta


class ScriptedLLM:
    """Plays back one scripted turn per call.

    A turn is a list of content blocks — tool calls, text, or both — so a
    scenario can drive an agent through an exact sequence of decisions.
    """

    def __init__(
        self,
        turns: list[list[ContentBlock]],
        *,
        capabilities: ModelCapabilities | None = None,
        usage: Usage | None = None,
    ) -> None:
        self.model = "scripted"
        self.capabilities = capabilities or ModelCapabilities(model_id="scripted")
        self._turns = list(turns)
        self._usage = usage or Usage(input_tokens=10, output_tokens=5)
        self.calls = 0
        self.seen: list[list[ChatMessage]] = []

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),  # noqa: B008
        ctx: Any = None,
    ) -> Any:
        raise NotImplementedError("scenarios drive the streaming path")

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),  # noqa: B008
        ctx: Any = None,
    ) -> AsyncIterator[CompletionEvent]:
        return self._stream(messages)

    async def _stream(
        self, messages: list[ChatMessage]
    ) -> AsyncIterator[CompletionEvent]:
        self.calls += 1
        self.seen.append(list(messages))
        turn = self._turns.pop(0) if self._turns else [TextBlock(text="done")]
        yield CompletionEvent(content=turn, usage=self._usage)

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return 0


class StreamingLLM:
    """Streams *chunks* text deltas, then the assembled completion."""

    def __init__(self, chunks: int) -> None:
        self.model = "streaming"
        self.capabilities = ModelCapabilities(model_id="streaming")
        self._chunks = chunks

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),  # noqa: B008
        ctx: Any = None,
    ) -> Any:
        raise NotImplementedError("scenarios drive the streaming path")

    def generate_stream(
        self,
        messages: list[ChatMessage],
        *,
        options: GenerationOptions = GenerationOptions(),  # noqa: B008
        ctx: Any = None,
    ) -> AsyncIterator[Any]:
        return self._stream()

    async def _stream(self) -> AsyncIterator[Any]:
        for _ in range(self._chunks):
            yield TextDelta(text="tok ")
        yield CompletionEvent(
            content=[TextBlock(text="tok " * self._chunks)],
            usage=Usage(input_tokens=10, output_tokens=self._chunks),
        )

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        return 0


__all__ = ["ScriptedLLM", "StreamingLLM"]
