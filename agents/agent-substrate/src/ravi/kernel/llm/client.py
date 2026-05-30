"""LLM client contracts."""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from ravi.kernel.content import ChatMessage, ContentBlock
from ravi.kernel.stream import CompletionEvent, ReasoningDelta, TextDelta
from ravi.kernel.tools import Tool


class LLMClient(Protocol):
    """Protocol for concrete LLM provider integrations."""

    model: str

    async def generate(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[Tool] | None = None,
        system: str = "",
        **kwargs: object,
    ) -> list[ContentBlock]:
        """Generate a complete response from the LLM."""
        ...

    async def generate_stream(
        self,
        messages: list[ChatMessage],
        **kwargs: object,
    ) -> AsyncIterator[TextDelta | ReasoningDelta | CompletionEvent]:
        """Stream a response from the LLM."""
        ...

    async def count_tokens(self, messages: list[ChatMessage]) -> int:
        """Estimate the token count for a list of messages."""
        ...


class EmbeddingClient(Protocol):
    """Protocol for text embedding generation."""

    async def embed_single(self, text: str) -> list[float]:
        """Generate an embedding for a single text string."""
        ...

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of text strings."""
        ...


from dataclasses import dataclass
from typing import Any

@dataclass
class EmbeddingResult:
    """Dataclass holding batch embedding results and metadata."""
    embeddings: list[list[float]]
    model: str
    usage_tokens: int = 0


class BaseEmbeddingClient:
    """Base class for concrete embedding client implementations."""

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
        """Generate embeddings for a list of texts (returning EmbeddingResult)."""
        raise NotImplementedError

    async def embed_single(self, text: str) -> list[float]:
        """Generate an embedding for a single text string."""
        res = await self.embed([text])
        return res.embeddings[0]

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of text strings."""
        res = await self.embed(texts)
        return res.embeddings
