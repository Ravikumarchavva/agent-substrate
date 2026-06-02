"""LLM client contracts — Protocol definitions only."""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from ravi.kernel.content import ChatMessage, ContentBlock
from ravi.kernel.stream import CompletionEvent, ReasoningDelta, TextDelta
from ravi.kernel.tools import Tool


class LLMClient(Protocol):
    """Contract every LLM provider adapter must satisfy."""

    model: str

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[Tool] | None = None,
        system: str = "",
        **kwargs: object,
    ) -> list[ContentBlock]: ...

    async def generate_stream(
        self,
        messages: list[ChatMessage],
        **kwargs: object,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]: ...

    async def count_tokens(self, messages: list[ChatMessage]) -> int: ...


class EmbeddingClient(Protocol):
    """Contract every embedding provider adapter must satisfy."""

    async def embed_single(self, text: str) -> list[float]: ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


__all__ = ["LLMClient", "EmbeddingClient"]
