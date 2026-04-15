"""Model client factory — create the right LLM client from a model string.

Convention (inspired by LiteLLM/Agentor but using native SDKs):
    - ``gpt-*``, ``o1-*``, ``o3-*``, ``openai/*``   → OpenAIClient
    - ``claude-*``, ``anthropic/*``                   → AnthropicClient
    - ``gemini-*``, ``gemini/*``, ``google/*``        → GeminiClient

Example::

    from raavan.integrations.llm.factory import create_model_client
    from raavan.core.llm.provider import ProviderConfig

    # Simple usage (auto-detect provider, pass api_keys dict)
    client = create_model_client("claude-sonnet-4-20250514", api_keys={
        "anthropic": "sk-ant-...",
    })

    # Advanced usage — OpenAI-compatible provider (vLLM, Ollama, etc.)
    client = create_model_client(
        "meta-llama/Llama-3-70B",
        provider_config=ProviderConfig(
            provider="openai",
            api_key="token-abc",
            base_url="http://localhost:8080/v1",
        ),
    )
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Optional

from raavan.core.llm.base_client import BaseModelClient
from raavan.core.llm.base_embedding_client import BaseEmbeddingClient

if TYPE_CHECKING:
    from raavan.core.llm.provider import ProviderConfig

logger = logging.getLogger(__name__)

# ── Provider detection ────────────────────────────────────────────────────────

OPENAI_PREFIXES = ("gpt-", "o1-", "o3-", "o4-", "dall-e", "whisper", "tts-")
ANTHROPIC_PREFIXES = ("claude-",)
GEMINI_PREFIXES = ("gemini-",)


def detect_provider(model: str) -> str:
    """Detect the LLM provider from a model string.

    Returns one of: ``"openai"``, ``"anthropic"``, ``"gemini"``.
    Raises ``ValueError`` for unrecognised model strings.
    """
    model_lower = model.lower().strip()

    # Explicit provider prefix: "provider/model-name"
    if "/" in model_lower:
        prefix = model_lower.split("/", 1)[0]
        if prefix in ("openai",):
            return "openai"
        if prefix in ("anthropic",):
            return "anthropic"
        if prefix in ("gemini", "google"):
            return "gemini"
        raise ValueError(
            f"Unknown provider prefix '{prefix}' in model string '{model}'. "
            f"Supported: openai/, anthropic/, gemini/, google/"
        )

    # Infer from model name
    for p in OPENAI_PREFIXES:
        if model_lower.startswith(p):
            return "openai"

    for p in ANTHROPIC_PREFIXES:
        if model_lower.startswith(p):
            return "anthropic"

    for p in GEMINI_PREFIXES:
        if model_lower.startswith(p):
            return "gemini"

    # Default to OpenAI for unrecognised models (most permissive)
    logger.warning(
        "Could not detect provider for model '%s', defaulting to OpenAI", model
    )
    return "openai"


def strip_provider_prefix(model: str) -> str:
    """Remove the ``provider/`` prefix if present."""
    if "/" in model:
        return model.split("/", 1)[1]
    return model


# ── Factory function ─────────────────────────────────────────────────────────


def create_model_client(
    model: str,
    *,
    provider_config: Optional[ProviderConfig] = None,
    api_keys: Optional[dict[str, str]] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    **kwargs: Any,
) -> BaseModelClient:
    """Create a model client for the given model string.

    Args:
        model: Model identifier (e.g. ``"gpt-5-mini"``, ``"claude-sonnet-4-20250514"``,
               ``"gemini/gemini-2.5-flash"``).
        provider_config: Structured provider configuration.  When provided,
            takes precedence over *api_keys*.  Use this to set ``base_url``
            for OpenAI-compatible providers (vLLM, Ollama, etc.).
        api_keys: Dict of provider API keys (backward-compatible shorthand),
                  e.g. ``{"openai": "sk-...", "anthropic": "sk-ant-..."}``.
        temperature: Default temperature for generation.
        max_tokens: Default max output tokens.
        **kwargs: Additional provider-specific arguments.

    Returns:
        A ``BaseModelClient`` instance for the detected provider.
    """
    if provider_config is not None:
        provider = provider_config.provider
        api_key = provider_config.api_key
        base_url = provider_config.base_url
        organization = provider_config.organization
        extra_headers = provider_config.extra_headers
        timeout = provider_config.timeout
    else:
        api_keys = api_keys or {}
        provider = detect_provider(model)
        api_key = api_keys.get(provider) or api_keys.get(
            "google" if provider == "gemini" else provider
        )
        base_url = None
        organization = None
        extra_headers = None
        timeout = None

    bare_model = strip_provider_prefix(model)

    if provider == "openai":
        from raavan.integrations.llm.openai.openai_client import OpenAIClient

        return OpenAIClient(
            model=bare_model,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            base_url=base_url,
            organization=organization,
            extra_headers=extra_headers,
            timeout=timeout,
            **kwargs,
        )

    if provider == "anthropic":
        from raavan.integrations.llm.anthropic.anthropic_client import AnthropicClient

        return AnthropicClient(
            model=bare_model,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    if provider == "gemini":
        from raavan.integrations.llm.gemini.gemini_client import GeminiClient

        return GeminiClient(
            model=bare_model,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )

    raise ValueError(f"Unsupported provider: {provider}")


# ── Embedding provider detection ──────────────────────────────────────────────

OPENAI_EMBEDDING_PREFIXES = ("text-embedding-",)
GEMINI_EMBEDDING_PREFIXES = ("text-embedding-004", "embedding-")


def detect_embedding_provider(model: str) -> str:
    """Detect the embedding provider from a model string.

    Returns one of: ``"openai"``, ``"gemini"``.
    """
    model_lower = model.lower().strip()

    # Explicit provider prefix: "provider/model-name"
    if "/" in model_lower:
        prefix = model_lower.split("/", 1)[0]
        if prefix in ("openai",):
            return "openai"
        if prefix in ("gemini", "google"):
            return "gemini"
        # Unknown prefix — default to OpenAI (most OpenAI-compatible servers
        # expose an /embeddings endpoint)
        logger.warning(
            "Unknown embedding provider prefix '%s', defaulting to OpenAI", prefix
        )
        return "openai"

    # Gemini's text-embedding-004 must be checked first (before OpenAI's
    # text-embedding-* wildcard)
    if model_lower == "text-embedding-004":
        return "gemini"

    for p in OPENAI_EMBEDDING_PREFIXES:
        if model_lower.startswith(p):
            return "openai"

    for p in GEMINI_EMBEDDING_PREFIXES:
        if model_lower.startswith(p):
            return "gemini"

    # Default to OpenAI for unrecognised models
    logger.warning(
        "Could not detect embedding provider for model '%s', defaulting to OpenAI",
        model,
    )
    return "openai"


# ── Embedding factory ─────────────────────────────────────────────────────────


def create_embedding_client(
    model: str = "text-embedding-3-small",
    *,
    provider_config: Optional[ProviderConfig] = None,
    api_keys: Optional[dict[str, str]] = None,
    dimensions: Optional[int] = None,
    **kwargs: Any,
) -> BaseEmbeddingClient:
    """Create an embedding client for the given model string.

    Args:
        model: Embedding model identifier (e.g. ``"text-embedding-3-small"``,
               ``"gemini/text-embedding-004"``).
        provider_config: Structured provider configuration.  When provided,
            takes precedence over *api_keys*.
        api_keys: Dict of provider API keys.
        dimensions: Default output dimensions (Matryoshka reduction).
        **kwargs: Additional provider-specific arguments.

    Returns:
        A ``BaseEmbeddingClient`` instance for the detected provider.
    """
    if provider_config is not None:
        provider = provider_config.provider
        api_key = provider_config.api_key
        base_url = provider_config.base_url
        organization = provider_config.organization
        extra_headers = provider_config.extra_headers
        timeout = provider_config.timeout
    else:
        api_keys = api_keys or {}
        provider = detect_embedding_provider(model)
        api_key = api_keys.get(provider) or api_keys.get(
            "google" if provider == "gemini" else provider
        )
        base_url = None
        organization = None
        extra_headers = None
        timeout = None

    bare_model = strip_provider_prefix(model)

    if provider == "openai":
        from raavan.integrations.llm.openai.openai_embedding_client import (
            OpenAIEmbeddingClient,
        )

        return OpenAIEmbeddingClient(
            model=bare_model,
            api_key=api_key,
            dimensions=dimensions,
            base_url=base_url,
            organization=organization,
            extra_headers=extra_headers,
            timeout=timeout,
            **kwargs,
        )

    if provider == "gemini":
        from raavan.integrations.llm.gemini.gemini_embedding_client import (
            GeminiEmbeddingClient,
        )

        return GeminiEmbeddingClient(
            model=bare_model,
            api_key=api_key,
            dimensions=dimensions,
            **kwargs,
        )

    raise ValueError(f"Unsupported embedding provider: {provider}")
