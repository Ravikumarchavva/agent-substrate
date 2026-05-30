"""Provider configuration — connection settings for LLM providers.

Separates *what to connect to* (base_url, api_key, headers) from *what model
to use*.  This lets the same ``OpenAIClient`` work with the OpenAI API,
vLLM, Ollama, Together, Perplexity, or any OpenAI-compatible endpoint.

    from ravi.kernel.llm.provider import ProviderConfig
    from ravi.adapters.llm.factory import create_model_client

    # Use vLLM with OpenAI-compatible API
    cfg = ProviderConfig(
        provider="openai",
        api_key="token-abc123",
        base_url="http://localhost:8080/v1",
    )
    client = create_model_client("meta-llama/Llama-3-70B", provider_config=cfg)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ProviderConfig:
    """Connection configuration for an LLM provider.

    Attributes:
        provider: Provider identifier — ``"openai"``, ``"anthropic"``,
            ``"gemini"``, or ``"openrouter"``.
        api_key: API key for the provider.  ``None`` means "use the default
            from environment variables" (provider SDK default behaviour).
        base_url: Override the default API endpoint.  Only meaningful for
            OpenAI-compatible providers (vLLM, Ollama, Together, etc.).
            ``None`` means "use the provider's default".
        organization: OpenAI organization header.  Ignored by other providers.
        extra_headers: Additional HTTP headers to include in every request.
        timeout: Request timeout in seconds.  ``None`` means "use SDK default".
    """

    provider: str
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    organization: Optional[str] = None
    extra_headers: Optional[dict[str, str]] = None
    timeout: Optional[float] = None
