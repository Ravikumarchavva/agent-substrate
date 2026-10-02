"""OpenAI embedding client — text-embedding-3-small/large and ada-002.

Supports the ``dimensions`` parameter for Matryoshka dimensionality
reduction on text-embedding-3-* models.  Also works with any
OpenAI-compatible endpoint (vLLM, Ollama, etc.) via ``base_url``.

Usage::

    from substrate.integrations.llm.openai.openai_embedding_client import (
        OpenAIEmbeddingClient,
    )

    client = OpenAIEmbeddingClient(api_key="sk-...")
    result = await client.embed(["Hello world"])
"""

from __future__ import annotations

import logging

from typing import Any, Optional

from openai import AsyncOpenAI

from substrate.integrations.llm.base import BaseEmbeddingClient, EmbeddingResult

logger = logging.getLogger(__name__)


# The Embeddings API accepts at most this many inputs in one request.
_MAX_INPUTS_PER_REQUEST = 2048


class OpenAIEmbeddingClient(BaseEmbeddingClient):
    """OpenAI Embeddings API client.

    Wraps ``AsyncOpenAI.embeddings.create()`` with batch support (the API
    accepts ``list[str]`` natively).
    """

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        dimensions: Optional[int] = None,
        *,
        base_url: Optional[str] = None,
        organization: Optional[str] = None,
        extra_headers: Optional[dict[str, str]] = None,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(model=model, dimensions=dimensions, max_input_tokens=8191, **kwargs)

        client_kwargs: dict[str, Any] = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url
        if organization:
            client_kwargs["organization"] = organization
        if extra_headers:
            client_kwargs["default_headers"] = extra_headers
        if timeout is not None:
            client_kwargs["timeout"] = timeout

        self.client = AsyncOpenAI(**client_kwargs)

    async def _embed_texts(self, texts: list[str], *, query: bool) -> EmbeddingResult:
        """Embed texts via the OpenAI Embeddings API (batch-native: a list of strings per call)."""
        create_kwargs: dict[str, Any] = {"model": self.model}
        # dimensions param is only supported on text-embedding-3-* models
        if self.dimensions is not None:
            create_kwargs["dimensions"] = self.dimensions

        embeddings: list[list[float]] = []
        usage_tokens = 0
        served_model = self.model
        for start in range(0, len(texts), _MAX_INPUTS_PER_REQUEST):
            response = await self.client.embeddings.create(**create_kwargs, input=texts[start : start + _MAX_INPUTS_PER_REQUEST])
            # Sort by index to guarantee order matches input
            embeddings.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
            usage_tokens += response.usage.total_tokens if response.usage else 0
            served_model = response.model

        if embeddings and self.dimensions is None:
            self.dimensions = len(embeddings[0])
        return EmbeddingResult(embeddings=embeddings, model=served_model, usage_tokens=usage_tokens)
