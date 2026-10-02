"""substrate.models — Chat and embedding models: their contracts, the capability registry, modality fitting, error classification."""

from __future__ import annotations

from substrate.models.client import (
    EmbeddingModel,
    ChatModel,
    Reranker,
)
from substrate.models.protocols import (
    EmbeddingResult,
    FinishReason,
    GenerationOptions,
    LLMResponse,
    Modality,
    ModelCapabilities,
    ReasoningEffort,
)
from substrate.models.registry import (
    MODEL_REGISTRY,
    ModelProfile,
    estimate_cost,
    get_model_profile,
    list_models,
)

__all__ = [
    "EmbeddingModel",
    "EmbeddingResult",
    "FinishReason",
    "GenerationOptions",
    "ChatModel",
    "LLMResponse",
    "MODEL_REGISTRY",
    "Modality",
    "ModelCapabilities",
    "ModelProfile",
    "ReasoningEffort",
    "Reranker",
    "estimate_cost",
    "get_model_profile",
    "list_models",
]
