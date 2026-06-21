"""OpenAI-compatible embedding client.

Implements the ``EmbeddingClient`` kernel Protocol using the standard
``/v1/embeddings`` API endpoint. Works with OpenAI, vLLM, Ollama,
or any other compatible service.
"""

from __future__ import annotations

import logging
from typing import Optional

from openai import AsyncOpenAI
from agent_substrate.kernel.llm import EmbeddingResult

logger = logging.getLogger(__name__)


class OpenAIEmbeddingClient:
    """Embedding client backed by OpenAI's /v1/embeddings API."""

    def __init__(
        self,
        model: str = "text-embedding-3-small",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        dimensions: Optional[int] = None,
        batch_size: int = 512,
    ) -> None:
        self.model = model
        self.dimensions = dimensions
        self.batch_size = batch_size

        client_kwargs = {}
        if api_key:
            client_kwargs["api_key"] = api_key
        if base_url:
            client_kwargs["base_url"] = base_url

        self.client = AsyncOpenAI(**client_kwargs)

    async def embed(self, texts: list[str]) -> EmbeddingResult:
        if not texts:
            return EmbeddingResult(embeddings=[], model=self.model, usage_tokens=0)

        embeddings: list[list[float]] = []
        total_tokens = 0

        # Process in batches
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            params = {
                "model": self.model,
                "input": batch,
            }
            if self.dimensions is not None:
                params["dimensions"] = self.dimensions

            response = await self.client.embeddings.create(**params)
            for item in response.data:
                embeddings.append(item.embedding)

            if response.usage:
                total_tokens += response.usage.total_tokens

        return EmbeddingResult(
            embeddings=embeddings,
            model=self.model,
            usage_tokens=total_tokens,
        )

    async def embed_single(self, text: str) -> list[float]:
        result = await self.embed([text])
        return result.embeddings[0]
