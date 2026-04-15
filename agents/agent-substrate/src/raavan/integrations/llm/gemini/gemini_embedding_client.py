"""Google Gemini embedding client — text-embedding-004.

Uses the ``google-genai`` SDK's ``embed_content`` endpoint.

Usage::

    from raavan.integrations.llm.gemini.gemini_embedding_client import (
        GeminiEmbeddingClient,
    )

    client = GeminiEmbeddingClient(api_key="...")
    result = await client.embed(["Hello world"])
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from google import genai

from raavan.core.llm.base_embedding_client import BaseEmbeddingClient, EmbeddingResult

logger = logging.getLogger(__name__)


class GeminiEmbeddingClient(BaseEmbeddingClient):
    """Google Gemini Embeddings API client.

    Wraps the ``google-genai`` unified SDK ``embed_content`` endpoint.
    The default model is ``text-embedding-004`` (768 dimensions).
    """

    def __init__(
        self,
        model: str = "text-embedding-004",
        api_key: Optional[str] = None,
        dimensions: Optional[int] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, dimensions=dimensions, **kwargs)
        self.api_key = api_key
        self.client = genai.Client(api_key=api_key)

    async def embed(
        self,
        texts: list[str],
        *,
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
    ) -> EmbeddingResult:
        """Embed texts via the Gemini Embeddings API.

        The ``google-genai`` SDK ``embed_content`` is sync-only, so we
        call it directly (it's a single HTTP round-trip, fast enough).
        """
        effective_model = model or self.model
        effective_dims = dimensions or self.dimensions

        config_kwargs: dict[str, Any] = {}
        if effective_dims is not None:
            config_kwargs["output_dimensionality"] = effective_dims

        config = (
            genai.types.EmbedContentConfig(**config_kwargs) if config_kwargs else None
        )

        response = self.client.models.embed_content(
            model=effective_model,
            contents=texts,
            config=config,
        )

        embeddings: list[list[float]] = []
        if response.embeddings:
            for emb in response.embeddings:
                embeddings.append(list(emb.values))

        return EmbeddingResult(
            embeddings=embeddings,
            model=effective_model,
            usage_tokens=0,  # Gemini doesn't report token usage for embeddings
        )
