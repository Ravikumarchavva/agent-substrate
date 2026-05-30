"""LLM client contracts and abstractions."""
from __future__ import annotations

import os

# Treat this module file as a package so it doesn't shadow the directory
__path__ = [os.path.join(os.path.dirname(__file__), "llm")]

from ravi.kernel.llm.client import LLMClient, EmbeddingClient, BaseEmbeddingClient, EmbeddingResult
from ravi.kernel.llm.models import (
    ModelProfile,
    MODEL_REGISTRY,
    get_model_profile,
    estimate_cost,
    list_models,
)
from ravi.kernel.llm.cache import SemanticCache
from ravi.kernel.llm.cached_client import CachedModelClient
from ravi.kernel.llm.fallback import FallbackClient
from ravi.kernel.llm.router import ComplexityTier, ModelRouter, RouteConstraints

__all__ = [
    "LLMClient",
    "EmbeddingClient",
    "BaseEmbeddingClient",
    "EmbeddingResult",
    "ModelProfile",
    "MODEL_REGISTRY",
    "get_model_profile",
    "estimate_cost",
    "list_models",
    "SemanticCache",
    "CachedModelClient",
    "FallbackClient",
    "ComplexityTier",
    "ModelRouter",
    "RouteConstraints",
]
