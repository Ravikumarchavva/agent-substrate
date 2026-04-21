"""Abstract base class for embedding clients.

All embedding providers (OpenAI, Gemini, etc.) implement this contract.
The pattern mirrors ``BaseModelClient`` — pure ABC in ``core/``, no
external SDK dependencies.

Usage::

    from ravi.core.llm.base_embedding_client import BaseEmbeddingClient

    class MyEmbeddingClient(BaseEmbeddingClient):
        async def embed(self, texts, *, model=None, dimensions=None):
            ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class EmbeddingResult:
    """Result from an embedding request.

    Attributes:
        embeddings: List of embedding vectors (one per input text).
        model: The model that produced the embeddings.
        usage_tokens: Total tokens consumed by the request.
    """

    embeddings: list[list[float]]
    model: str
    usage_tokens: int = 0


class BaseEmbeddingClient(ABC):
    """Base class for all embedding clients.

    Subclasses must implement ``embed`` — all other convenience methods
    delegate to it.
    """

    def __init__(
        self,
        model: str,
        dimensions: Optional[int] = None,
        **kwargs: object,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self.kwargs = kwargs

    # ── Abstract ──────────────────────────────────────────────────────────────

    @abstractmethod
    async def embed(
        self,
        texts: list[str],
        *,
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
    ) -> EmbeddingResult:
        """Embed a batch of texts and return their vectors.

        Args:
            texts: Input texts to embed.
            model: Override the default model for this call.
            dimensions: Override the default dimensions for this call
                (Matryoshka reduction — not all providers support this).

        Returns:
            An ``EmbeddingResult`` containing the embeddings and usage info.
        """
        ...

    # ── Convenience ───────────────────────────────────────────────────────────

    async def embed_single(
        self,
        text: str,
        *,
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
    ) -> list[float]:
        """Embed a single text and return its vector."""
        result = await self.embed([text], model=model, dimensions=dimensions)
        return result.embeddings[0]
