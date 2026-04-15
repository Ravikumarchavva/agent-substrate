"""raavan.core.llm — Abstract LLM client contract (text, vision, audio, embeddings)."""

from raavan.core.llm.base_client import (
    BaseModelClient,
    STT_MODEL,
    TTS_VOICE,
    TTS_FORMAT,
)
from raavan.core.llm.base_embedding_client import (
    BaseEmbeddingClient,
    EmbeddingResult,
)
from raavan.core.llm.models import (
    ModelProfile,
    MODEL_REGISTRY,
    get_model_profile,
    get_context_length,
    estimate_cost,
    list_models,
)
from raavan.core.llm.provider import ProviderConfig

__all__ = [
    "BaseModelClient",
    "BaseEmbeddingClient",
    "EmbeddingResult",
    "STT_MODEL",
    "TTS_VOICE",
    "TTS_FORMAT",
    "ModelProfile",
    "MODEL_REGISTRY",
    "get_model_profile",
    "get_context_length",
    "estimate_cost",
    "list_models",
    "ProviderConfig",
]
