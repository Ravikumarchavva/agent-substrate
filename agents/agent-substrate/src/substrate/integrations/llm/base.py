"""Convenience base for concrete embedding adapter implementations."""

from __future__ import annotations

from typing import Any

from substrate.kernel.llm import EmbeddingResult


class BaseEmbeddingClient:
    """Convenience base for concrete embedding provider integrations.

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
