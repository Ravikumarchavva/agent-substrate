"""loamz.agents.providers.llm — LLM provider clients and factory.

Quick-start
-----------
Any provider by model name::

    from loamz.agents.providers.llm import LLMFactory

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

    from loamz.agents.providers.llm import OpenAICompatibleClient

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
    "LLMFactory": "loamz.agents.providers.llm.factory",
    "build_client": "loamz.agents.providers.llm.factory",
    "create_model_client": "loamz.agents.providers.llm.factory",
    "create_embedding_client": "loamz.agents.providers.llm.factory",
    "detect_provider": "loamz.agents.providers.llm.factory",
    "detect_embedding_provider": "loamz.agents.providers.llm.factory",
    "strip_provider_prefix": "loamz.agents.providers.llm.factory",
    "model_supports_vision": "loamz.agents.providers.llm.factory",
    "has_provider_api_key": "loamz.agents.providers.llm.factory",
    "resolve_model_for_available_credentials": "loamz.agents.providers.llm.factory",
    "resolve_vision_model_for_available_credentials": "loamz.agents.providers.llm.factory",
    "CHAT_MODEL_FALLBACKS": "loamz.agents.providers.llm.factory",
    "VISION_MODEL_FALLBACKS": "loamz.agents.providers.llm.factory",
    "OpenAIClient": "loamz.agents.providers.llm.openai",
    "OpenAIEmbeddingClient": "loamz.agents.providers.llm.openai",
    "SentenceTransformersEmbeddingClient": "loamz.agents.providers.llm.local_embeddings",
    "OpenAICompatibleClient": "loamz.agents.providers.llm.openai_compatible",
}

if TYPE_CHECKING:
    from loamz.agents.providers.llm.factory import (
        CHAT_MODEL_FALLBACKS,
        VISION_MODEL_FALLBACKS,
        LLMFactory,
        build_client,
        create_embedding_client,
        create_model_client,
        detect_embedding_provider,
        detect_provider,
        has_provider_api_key,
        model_supports_vision,
        resolve_model_for_available_credentials,
        resolve_vision_model_for_available_credentials,
        strip_provider_prefix,
    )
    from loamz.agents.providers.llm.local_embeddings import SentenceTransformersEmbeddingClient
    from loamz.agents.providers.llm.openai import OpenAIClient, OpenAIEmbeddingClient
    from loamz.agents.providers.llm.openai_compatible import OpenAICompatibleClient

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
