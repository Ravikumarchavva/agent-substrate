"""LLM extras — caching, fallback, routing on top of BaseModelClient."""

from ravi.extensions.llm.cache import SemanticCache
from ravi.extensions.llm.cached_client import CachedModelClient
from ravi.extensions.llm.fallback import FallbackClient
from ravi.extensions.llm.router import ComplexityTier, ModelRouter, RouteConstraints

__all__ = [
    "SemanticCache",
    "CachedModelClient",
    "FallbackClient",
    "ComplexityTier",
    "ModelRouter",
    "RouteConstraints",
]
