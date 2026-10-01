"""substrate.agents.llm — kernel LLMClient/EmbeddingClient contracts, model
metadata, and the one default implementation of each.

``OpenAIChatCompletionClient`` and ``SentenceTransformersEmbeddingClient``
are the L1 defaults — the same "one implementation needing the least
infrastructure the Protocol can possibly need" rule every other storage
Protocol already follows. Additional vendor-native clients and provider
auto-detection live in ``integrations/llm/`` for L2.
"""

from __future__ import annotations

from substrate.agents.llm.client import LLMClient, EmbeddingClient
from substrate.agents.llm.models import (
    ModelProfile,
    MODEL_REGISTRY,
    get_model_profile,
    estimate_cost,
    list_models,
)
from substrate.agents.llm.chat_client import OpenAIChatCompletionClient
from substrate.agents.llm.embedding_client import SentenceTransformersEmbeddingClient

__all__ = [
    "LLMClient",
    "EmbeddingClient",
    "ModelProfile",
    "MODEL_REGISTRY",
    "get_model_profile",
    "estimate_cost",
    "list_models",
    "OpenAIChatCompletionClient",
    "SentenceTransformersEmbeddingClient",
]
