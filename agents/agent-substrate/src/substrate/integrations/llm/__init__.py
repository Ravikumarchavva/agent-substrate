"""substrate.integrations.llm — LLM provider clients and factory.

Quick-start
-----------
Any provider by model name::

    from substrate.integrations.llm import LLMFactory

    # Cloud providers — auto-detected from prefix
    client = LLMFactory("gpt-4o", api_key).build()
    client = LLMFactory("anthropic/claude-sonnet-4-20250514", api_key).build()
    client = LLMFactory("groq/llama-3.3-70b-versatile", api_key).build()
    client = LLMFactory("ollama/llama3.2", api_key="").build()   # local, no key
    client = LLMFactory("together/meta-llama/Meta-Llama-3.1-8B", api_key).build()
    client = LLMFactory("mistral/mistral-large-latest", api_key).build()
    client = LLMFactory("deepseek/deepseek-chat", api_key).build()

    # Generic OpenAI-compatible server (vLLM, LM Studio, custom)
    client = LLMFactory("compatible/my-model", "none").build(
        base_url="http://localhost:8000/v1"
    )

Direct client construction::

    from substrate.integrations.llm import OpenAICompatibleClient

    # Points at Ollama running locally
    client = OpenAICompatibleClient(
        model="llama3.2",
        api_key="ollama",
        base_url="http://localhost:11434/v1",
    )
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

# Imported when asked for, not when the package is: each vendor's client needs only its own SDK (``openai``,
# ``anthropic``, ``gemini`` extras), so asking for one must not require the others.
_LAZY: dict[str, str] = {
    "LLMFactory": "substrate.integrations.llm.factory",
    "build_client": "substrate.integrations.llm.factory",
    "create_model_client": "substrate.integrations.llm.factory",
    "create_embedding_client": "substrate.integrations.llm.factory",
    "detect_provider": "substrate.integrations.llm.factory",
    "detect_embedding_provider": "substrate.integrations.llm.factory",
    "strip_provider_prefix": "substrate.integrations.llm.factory",
    "model_supports_vision": "substrate.integrations.llm.factory",
    "has_provider_api_key": "substrate.integrations.llm.factory",
    "resolve_model_for_available_credentials": "substrate.integrations.llm.factory",
    "resolve_vision_model_for_available_credentials": "substrate.integrations.llm.factory",
    "CHAT_MODEL_FALLBACKS": "substrate.integrations.llm.factory",
    "VISION_MODEL_FALLBACKS": "substrate.integrations.llm.factory",
    "OpenAIClient": "substrate.integrations.llm.openai",
    "OpenAIEmbeddingClient": "substrate.integrations.llm.openai",
    "SentenceTransformersEmbeddingClient": "substrate.integrations.llm.local_embeddings",
    "OpenAICompatibleClient": "substrate.integrations.llm.openai_compatible",
}

if TYPE_CHECKING:
    from substrate.integrations.llm.factory import LLMFactory
    from substrate.integrations.llm.factory import build_client
    from substrate.integrations.llm.factory import create_model_client
    from substrate.integrations.llm.factory import create_embedding_client
    from substrate.integrations.llm.factory import detect_provider
    from substrate.integrations.llm.factory import detect_embedding_provider
    from substrate.integrations.llm.factory import strip_provider_prefix
    from substrate.integrations.llm.factory import model_supports_vision
    from substrate.integrations.llm.factory import has_provider_api_key
    from substrate.integrations.llm.factory import (
        resolve_model_for_available_credentials,
    )
    from substrate.integrations.llm.factory import (
        resolve_vision_model_for_available_credentials,
    )
    from substrate.integrations.llm.factory import CHAT_MODEL_FALLBACKS
    from substrate.integrations.llm.factory import VISION_MODEL_FALLBACKS
    from substrate.integrations.llm.openai import OpenAIClient
    from substrate.integrations.llm.openai import OpenAIEmbeddingClient
    from substrate.integrations.llm.local_embeddings import (
        SentenceTransformersEmbeddingClient,
    )
    from substrate.integrations.llm.openai_compatible import OpenAICompatibleClient

__all__ = [
    # Factory
    "LLMFactory",
    "build_client",
    "create_model_client",
    "create_embedding_client",
    "detect_provider",
    "detect_embedding_provider",
    "strip_provider_prefix",
    "model_supports_vision",
    "has_provider_api_key",
    "resolve_model_for_available_credentials",
    "resolve_vision_model_for_available_credentials",
    "CHAT_MODEL_FALLBACKS",
    "VISION_MODEL_FALLBACKS",
    # Concrete clients
    "OpenAIClient",
    "OpenAICompatibleClient",
    "OpenAIEmbeddingClient",
    "SentenceTransformersEmbeddingClient",
]


def __getattr__(name: str) -> object:
    if name in _LAZY:
        value = getattr(importlib.import_module(_LAZY[name]), name)
        globals()[name] = value
        return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
