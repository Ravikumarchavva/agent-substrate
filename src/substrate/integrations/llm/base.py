"""Convenience base for concrete embedding adapter implementations."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from substrate.models import EmbeddingResult, Modality
from substrate.types import ContentBlock, MediaBlock, TextBlock, UnsupportedContentError


class BaseEmbeddingClient:
    """Convenience base for text-only embedding provider integrations.

    Subclasses implement ``_embed_texts``; ``embed`` turns each input — a string, or content blocks joined as text — into the string
    that goes to the vendor, refusing (never dropping) a block it cannot embed. This satisfies the ``EmbeddingModel`` Protocol.
    """

    modalities: frozenset[Modality] = frozenset({Modality.TEXT})
    max_input_tokens: int = 512

    def __init__(
        self,
        model: str,
        dimensions: int | None = None,
        *,
        max_input_tokens: int | None = None,
        **kwargs: Any,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        if max_input_tokens is not None:
            self.max_input_tokens = max_input_tokens

    async def embed(
        self, inputs: Sequence[str | Sequence[ContentBlock]], *, query: bool = False
    ) -> EmbeddingResult:
        texts = [_text_of(item) for item in inputs]
        if not texts:
            return EmbeddingResult(
                embeddings=[], model=self.model
            )  # the vendors reject an empty input
        return await self._embed_texts(texts, query=query)

    async def _embed_texts(self, texts: list[str], *, query: bool) -> EmbeddingResult:
        raise NotImplementedError


def _text_of(item: str | Sequence[ContentBlock]) -> str:
    if isinstance(item, str):
        return item
    for block in item:
        if isinstance(block, MediaBlock):
            raise UnsupportedContentError(
                "this embedding client takes text only; resolve media to text (a caption, OCR) before embedding"
            )
        if not isinstance(block, TextBlock):
            raise UnsupportedContentError(f"cannot embed a {type(block).__name__}")
    return "".join(block.text for block in item if isinstance(block, TextBlock))


__all__ = ["EmbeddingResult", "BaseEmbeddingClient"]
