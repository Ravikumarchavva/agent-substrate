"""OpenAI embedding client — text-embedding-3-small/large and ada-002.

Supports the ``dimensions`` parameter for Matryoshka dimensionality
reduction on text-embedding-3-* models.  Also works with any
OpenAI-compatible endpoint (vLLM, Ollama, etc.) via ``base_url``.

Usage::

    from ravi.integrations.llm.openai.openai_embedding_client import (
        OpenAIEmbeddingClient,
    )

    client = OpenAIEmbeddingClient(api_key="sk-...")
    result = await client.embed(["Hello world"], dimensions=256)
"""

from __future__ import annotations
from ravi.logger import setup_logging

from typing import Any, Optional

from openai import AsyncOpenAI

from ravi.kernel.llm.base_embedding_client import BaseEmbeddingClient, EmbeddingResult

logger = setup_logging()


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
        super().__init__(model=model, dimensions=dimensions, **kwargs)

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

    async def embed(
        self,
        texts: list[str],
        *,
        model: Optional[str] = None,
        dimensions: Optional[int] = None,
    ) -> EmbeddingResult:
        """Embed texts via the OpenAI Embeddings API.

        The API accepts a list of strings in a single call (batch-native).
        """
        effective_model = model or self.model
        effective_dims = dimensions or self.dimensions

        create_kwargs: dict[str, Any] = {
            "model": effective_model,
            "input": texts,
        }
        # dimensions param is only supported on text-embedding-3-* models
        if effective_dims is not None:
            create_kwargs["dimensions"] = effective_dims

        response = await self.client.embeddings.create(**create_kwargs)

        # Sort by index to guarantee order matches input
        sorted_data = sorted(response.data, key=lambda d: d.index)
        embeddings = [item.embedding for item in sorted_data]

        usage_tokens = response.usage.total_tokens if response.usage else 0

        return EmbeddingResult(
            embeddings=embeddings,
            model=response.model,
            usage_tokens=usage_tokens,
        )
