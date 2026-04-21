"""Model client factory — create the right LLM client from a model string.

Convention (inspired by LiteLLM/Agentor but using native SDKs):
    - ``gpt-*``, ``o1-*``, ``o3-*``, ``openai/*``   → OpenAIClient
    - ``groq/*``                                     → OpenAIClient via Groq
    - ``claude-*``, ``anthropic/*``                   → AnthropicClient
    - ``gemini-*``, ``gemini/*``, ``google/*``        → GeminiClient
    - ``openrouter/*``                                → OpenAIClient via OpenRouter

Example::

    from ravi.integrations.llm.factory import create_model_client
    from ravi.core.llm.provider import ProviderConfig

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

from ravi.core.llm.base_client import BaseModelClient
from ravi.core.llm.base_embedding_client import BaseEmbeddingClient
from ravi.core.llm.models import get_model_profile

if TYPE_CHECKING:
    from ravi.core.llm.provider import ProviderConfig

logger = logging.getLogger(__name__)

# ── Provider detection ────────────────────────────────────────────────────────

OPENAI_PREFIXES = ("gpt-", "o1-", "o3-", "o4-", "dall-e", "whisper", "tts-")
ANTHROPIC_PREFIXES = ("claude-",)
GEMINI_PREFIXES = ("gemini-",)
CHAT_MODEL_FALLBACKS = (
    "openai/gpt-5.4-mini",
    "google/gemini-2.5-flash",
    "groq/llama-3.3-70b-versatile",
    "openrouter/liquid/lfm-2.5-1.2b-thinking:free",
    "anthropic/claude-sonnet-4-20250514",
)
VISION_MODEL_FALLBACKS = (
    "google/gemini-2.5-flash",
    "openrouter/openai/gpt-4o-mini",
    "openai/gpt-4o-mini",
    "anthropic/claude-3-5-sonnet-20241022",
)


def detect_provider(model: str) -> str:
    """Detect the LLM provider from a model string.

    Returns one of: ``"openai"``, ``"groq"``, ``"anthropic"``,
    ``"gemini"``, ``"openrouter"``.
    Raises ``ValueError`` for unrecognised model strings.
    """
    model_lower = model.lower().strip()

    # Explicit provider prefix: "provider/model-name"
    if "/" in model_lower:
        prefix = model_lower.split("/", 1)[0]
        if prefix in ("openai",):
            return "openai"
        if prefix in ("groq",):
            return "groq"
        if prefix in ("anthropic",):
            return "anthropic"
        if prefix in ("gemini", "google"):
            return "gemini"
        if prefix in ("openrouter",):
            return "openrouter"
        raise ValueError(
            f"Unknown provider prefix '{prefix}' in model string '{model}'. "
            f"Supported: openai/, groq/, anthropic/, gemini/, google/, openrouter/"
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
    """Remove the leading ``provider/`` prefix if present.

    For OpenRouter-routed models this preserves the routed model ID, e.g.
    ``openrouter/liquid/lfm-2.5-1.2b-thinking:free`` becomes
    ``liquid/lfm-2.5-1.2b-thinking:free``.
    """
    if "/" in model:
        return model.split("/", 1)[1]
    return model


def provider_api_key_name(provider: str) -> str:
    """Return the api_keys lookup key for a provider name."""
    if provider == "gemini":
        return "google"
    return provider


def _model_profile_lookup_name(model: str) -> str:
    """Collapse provider prefixes to the canonical registry key."""
    lookup = model.strip()
    while "/" in lookup:
        provider = lookup.split("/", 1)[0]
        if provider not in (
            "openai",
            "groq",
            "anthropic",
            "gemini",
            "google",
            "openrouter",
        ):
            break
        lookup = strip_provider_prefix(lookup)
    return lookup


def model_supports_vision(model: str) -> bool:
    """Return True when the model is known to accept image inputs."""
    profile = get_model_profile(_model_profile_lookup_name(model))
    return bool(profile and profile.supports_vision)


def has_provider_api_key(
    provider: str,
    api_keys: Optional[dict[str, str]] = None,
) -> bool:
    """Return True when credentials are configured for the provider."""
    keys = api_keys or {}
    key_name = provider_api_key_name(provider)
    return bool((keys.get(key_name) or "").strip())


def resolve_model_for_available_credentials(
    model: str,
    *,
    api_keys: Optional[dict[str, str]] = None,
    fallback_models: Optional[list[str] | tuple[str, ...]] = None,
) -> str:
    """Return the first model whose provider has configured credentials."""
    candidates: list[str] = []
    seen: set[str] = set()

    for candidate in (model, *(fallback_models or ())):
        normalized = candidate.strip()
        if not normalized or normalized in seen:
            continue
        candidates.append(normalized)
        seen.add(normalized)

    for candidate in candidates:
        provider = detect_provider(candidate)
        if has_provider_api_key(provider, api_keys):
            return candidate

    return model


def resolve_vision_model_for_available_credentials(
    model: str,
    *,
    api_keys: Optional[dict[str, str]] = None,
    fallback_models: Optional[list[str] | tuple[str, ...]] = None,
) -> str:
    """Resolve to a credentialed model that is known to support vision."""
    resolved_model = resolve_model_for_available_credentials(
        model,
        api_keys=api_keys,
        fallback_models=fallback_models,
    )
    if model_supports_vision(resolved_model):
        return resolved_model

    candidates: list[str] = []
    seen: set[str] = set()
    for candidate in (
        resolved_model,
        model,
        *(fallback_models or ()),
        *VISION_MODEL_FALLBACKS,
    ):
        normalized = candidate.strip()
        if not normalized or normalized in seen:
            continue
        candidates.append(normalized)
        seen.add(normalized)

    for candidate in candidates:
        provider = detect_provider(candidate)
        if not has_provider_api_key(provider, api_keys):
            continue
        if model_supports_vision(candidate):
            return candidate

    return resolved_model


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
    openai_base_url = kwargs.pop("openai_base_url", None)
    groq_base_url = kwargs.pop("groq_base_url", None)
    openrouter_base_url = kwargs.pop("openrouter_base_url", None)
    openrouter_site_url = kwargs.pop("openrouter_site_url", None)
    openrouter_app_name = kwargs.pop("openrouter_app_name", None)

    openrouter_headers = {
        key: value
        for key, value in {
            "HTTP-Referer": openrouter_site_url,
            "X-Title": openrouter_app_name,
        }.items()
        if value
    }

    if provider_config is not None:
        provider = provider_config.provider
        api_key = provider_config.api_key
        base_url = provider_config.base_url
        organization = provider_config.organization
        extra_headers = provider_config.extra_headers
        timeout = provider_config.timeout

        if provider == "groq":
            base_url = base_url or groq_base_url or "https://api.groq.com/openai/v1"
        elif provider == "openrouter":
            base_url = base_url or openrouter_base_url or "https://openrouter.ai/api/v1"
            if openrouter_headers:
                extra_headers = {**openrouter_headers, **(extra_headers or {})}
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

        if provider == "openai":
            base_url = openai_base_url or None
        elif provider == "groq":
            base_url = groq_base_url or "https://api.groq.com/openai/v1"
        elif provider == "openrouter":
            base_url = openrouter_base_url or "https://openrouter.ai/api/v1"
            extra_headers = openrouter_headers or None

    bare_model = strip_provider_prefix(model)

    if provider in ("openai", "groq", "openrouter"):
        from ravi.integrations.llm.openai.openai_client import OpenAIClient

        client_cls: type = OpenAIClient
        if provider in ("groq", "openrouter"):
            from ravi.integrations.llm.openai.openai_chat_client import (
                OpenAIChatCompletionClient,
            )

            client_cls = OpenAIChatCompletionClient

        client = client_cls(
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
        setattr(client, "provider", provider)
        return client

    if provider == "anthropic":
        from ravi.integrations.llm.anthropic.anthropic_client import AnthropicClient

        client = AnthropicClient(
            model=bare_model,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        setattr(client, "provider", provider)
        return client

    if provider == "gemini":
        from ravi.integrations.llm.gemini.gemini_client import GeminiClient

        client = GeminiClient(
            model=bare_model,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            **kwargs,
        )
        setattr(client, "provider", provider)
        return client

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
        from ravi.integrations.llm.openai.openai_embedding_client import (
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
        from ravi.integrations.llm.gemini.gemini_embedding_client import (
            GeminiEmbeddingClient,
        )

        return GeminiEmbeddingClient(
            model=bare_model,
            api_key=api_key,
            dimensions=dimensions,
            **kwargs,
        )

    raise ValueError(f"Unsupported embedding provider: {provider}")
