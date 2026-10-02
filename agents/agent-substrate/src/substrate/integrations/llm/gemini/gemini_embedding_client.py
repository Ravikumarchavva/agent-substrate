"""Google Gemini embedding client — text-embedding-004.

Uses the ``google-genai`` SDK's ``embed_content`` endpoint.

Usage::

    from substrate.integrations.llm.gemini.gemini_embedding_client import (
        GeminiEmbeddingClient,
    )

    client = GeminiEmbeddingClient(api_key="...")
    result = await client.embed(["Hello world"])
"""

from __future__ import annotations

import logging

from typing import Any, Optional

from google import genai

from substrate.integrations.llm.base import BaseEmbeddingClient, EmbeddingResult

logger = logging.getLogger(__name__)


# ``batchEmbedContents`` accepts at most this many inputs in one request.
_MAX_INPUTS_PER_REQUEST = 100


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
        super().__init__(model=model, dimensions=dimensions, max_input_tokens=2048, **kwargs)
        self.api_key = api_key
        self.client = genai.Client(api_key=api_key)

    async def _embed_texts(self, texts: list[str], *, query: bool) -> EmbeddingResult:
        """Embed texts via the Gemini Embeddings API (``query`` selects the retrieval-query task type)."""
        effective_model = self.model
        effective_dims = self.dimensions
        config_kwargs: dict[str, Any] = {}
        if effective_dims is not None:
            config_kwargs["output_dimensionality"] = effective_dims
        config_kwargs["task_type"] = "RETRIEVAL_QUERY" if query else "RETRIEVAL_DOCUMENT"
        config = genai.types.EmbedContentConfig(**config_kwargs)

        embeddings: list[list[float]] = []
        for start in range(0, len(texts), _MAX_INPUTS_PER_REQUEST):
            # The async API: the sync one would block the event loop for the whole round trip.
            response = await self.client.aio.models.embed_content(
                model=effective_model,
                contents=texts[start : start + _MAX_INPUTS_PER_REQUEST],  # type: ignore[arg-type]
                config=config,
            )
            vectors = [list(e.values) for e in (response.embeddings or []) if e.values is not None]
            if len(vectors) != len(texts[start : start + _MAX_INPUTS_PER_REQUEST]):
                raise ValueError(
                    f"Gemini returned {len(vectors)} embeddings for {len(texts[start : start + _MAX_INPUTS_PER_REQUEST])} texts"
                )
            embeddings.extend(vectors)

        if embeddings and self.dimensions is None:
            self.dimensions = len(embeddings[0])
        return EmbeddingResult(
            embeddings=embeddings,
            model=effective_model,
            usage_tokens=0,  # Gemini doesn't report token usage for embeddings
        )
