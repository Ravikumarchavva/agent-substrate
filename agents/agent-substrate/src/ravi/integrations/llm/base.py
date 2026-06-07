"""Convenience base for concrete embedding adapter implementations.

``BaseEmbeddingClient`` and ``EmbeddingResult`` live here — in the adapters
layer — because they are implementation helpers, not kernel contracts.
The kernel only defines the ``EmbeddingClient`` Protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


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

    def __init__(
        self, model: str, dimensions: int | None = None, **kwargs: Any
    ) -> None:
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


__all__ = ["EmbeddingResult", "BaseEmbeddingClient"]
