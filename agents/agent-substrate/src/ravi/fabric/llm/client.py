from __future__ import annotations

from typing import Protocol, AsyncIterator

from ravi.kernel import ContentBlock, Tool, ChatMessage
from ravi.kernel.stream import TextDelta, ReasoningDelta, CompletionEvent


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
