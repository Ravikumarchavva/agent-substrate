"""Wire schemas of the embedding-reranker service: the OpenAI embeddings wire and the Jina/Cohere rerank wire.

``POST /v1/embeddings`` takes ``input`` as a list of items, each a string, ``{"image": "<base64>"}``, or ``{"content": [<string or image>, …]}``
(one vector for the whole mixed item); the model's ``dimensions``, ``max_input_tokens`` and ``modalities`` are at ``GET /v1/models``.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EmbeddingsRequest(BaseModel):
    input: list[str | dict[str, Any]] = Field(min_length=1)
    model: str = ""
    input_type: str = ""
    """``"query"`` for a search query (the server prepends the model's query instruction); anything else is a passage."""


class EmbeddingRow(BaseModel):
    object: str = "embedding"
    index: int
    embedding: list[float]


class EmbeddingsResponse(BaseModel):
    object: str = "list"
    data: list[EmbeddingRow]
    model: str
    usage: dict[str, int] = Field(default_factory=lambda: {"prompt_tokens": 0, "total_tokens": 0})


class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    model: str = ""
    top_n: int | None = None


class RerankRow(BaseModel):
    index: int
    relevance_score: float


class RerankResponse(BaseModel):
    results: list[RerankRow]
    model: str


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    dimensions: int
    max_input_tokens: int
    modalities: list[str]


class ModelsResponse(BaseModel):
    object: str = "list"
    data: list[ModelInfo]


class HealthResponse(BaseModel):
    status: str
    pod_name: str
    uptime_seconds: float
