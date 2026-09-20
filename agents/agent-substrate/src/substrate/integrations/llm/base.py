"""Convenience base for concrete embedding adapter implementations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from substrate.kernel.core.content import ContentBlock
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

    async def embed_blocks(self, blocks: Sequence[ContentBlock]) -> list[float]:
        from substrate.kernel.core.content import content_blocks_to_str

        text = content_blocks_to_str(blocks)
        return await self.embed_single(text)


__all__ = ["EmbeddingResult", "BaseEmbeddingClient"]
