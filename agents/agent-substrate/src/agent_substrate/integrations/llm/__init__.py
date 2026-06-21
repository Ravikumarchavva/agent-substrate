"""agent_substrate.integrations.llm — LLM provider clients and factory.

Quick-start
-----------
Any provider by model name::

    from agent_substrate.integrations.llm import LLMFactory

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

    # Universal client lives in capabilities
    from agent_substrate.capabilities.llm import OpenAIChatCompletionClient

    # Points at Ollama running locally
    client = OpenAIChatCompletionClient(
        model="llama3.2",
        api_key="ollama",
        base_url="http://localhost:11434/v1",
    )
"""

from __future__ import annotations

from agent_substrate.integrations.llm.factory import (
    LLMFactory,
    build_client,
    create_model_client,
    create_embedding_client,
    detect_provider,
    detect_embedding_provider,
    strip_provider_prefix,
    model_supports_vision,
    has_provider_api_key,
    resolve_model_for_available_credentials,
    resolve_vision_model_for_available_credentials,
    CHAT_MODEL_FALLBACKS,
    VISION_MODEL_FALLBACKS,
)
from agent_substrate.integrations.llm.openai import (
    OpenAIClient,
    OpenAIEmbeddingClient,
)

# Universal clients live in capabilities (no external API dependency)
from agent_substrate.capabilities.llm import (
    OpenAIChatCompletionClient,
    SentenceTransformersEmbeddingClient,
)

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
    "OpenAIChatCompletionClient",
    "OpenAIEmbeddingClient",
    "SentenceTransformersEmbeddingClient",
]
