"""LLM client contracts — Protocol definitions only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncIterator, Protocol

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


@dataclass
class EmbeddingResult:
    """Batch embedding results and token usage metadata."""

    embeddings: list[list[float]]
    model: str
    usage_tokens: int = 0


class BaseEmbeddingClient:
    """Convenience base for concrete embedding provider adapters.

    Subclasses implement ``embed()``; ``embed_single`` and ``embed_batch``
    are derived from it, satisfying the ``EmbeddingClient`` Protocol.
    """

    def __init__(self, model: str, dimensions: int | None = None, **kwargs: Any) -> None:
        self.model = model
        self.dimensions = dimensions

    async def embed(
        self,
        texts: list[str],
        *,
        model: str | None = None,
        dimensions: int | None = None,
    ) -> EmbeddingResult:
        raise NotImplementedError

    async def embed_single(self, text: str) -> list[float]:
        res = await self.embed([text])
        return res.embeddings[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        res = await self.embed(texts)
        return res.embeddings


__all__ = ["LLMClient", "EmbeddingClient", "EmbeddingResult", "BaseEmbeddingClient"]
