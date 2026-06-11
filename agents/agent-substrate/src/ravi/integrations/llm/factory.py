"""LLMFactory — build any LLM client from a single (model, api_key) pair.

    factory = LLMFactory("claude-sonnet-4-20250514", "sk-ant-...")
    client  = factory.build()
    cost    = factory.estimate_cost(input_tokens=1_000, output_tokens=500)

Provider is auto-detected from the model string.  No guessing, no dict of keys,
no kwargs soup.  One model, one key — done.

System instructions are NOT passed through this layer.  They travel as an
explicit ``system_instructions=`` kwarg on every ``generate()`` call (see
``LLMClient.generate``).  The factory only handles connection wiring.

Provider / model / cost table lives in ``ravi.kernel.llm.models``.

OpenAI-compatible providers
----------------------------
The following provider prefixes all route to ``OpenAIChatCompletionClient``
(standard ``/v1/chat/completions`` endpoint):

    Cloud — require an API key:
        groq/           https://api.groq.com/openai/v1
        openrouter/     https://openrouter.ai/api/v1
        together/       https://api.together.xyz/v1
        fireworks/      https://api.fireworks.ai/inference/v1
        perplexity/     https://api.perplexity.ai
        mistral/        https://api.mistral.ai/v1
        deepseek/       https://api.deepseek.com/v1
        cerebras/       https://api.cerebras.ai/v1
        sambanova/      https://api.sambanova.ai/v1
        nvidia/         https://integrate.api.nvidia.com/v1

    Local — no real API key needed (pass "" or any non-empty string):
        ollama/         http://localhost:11434/v1
        lmstudio/       http://localhost:1234/v1
        vllm/           http://localhost:8000/v1  (override with base_url=)

    Generic catch-all — you supply the base_url:
        compatible/     (base_url kwarg required)

Examples::

    # Named cloud provider
    client = LLMFactory("groq/llama-3.3-70b-versatile", groq_key).build()
    client = LLMFactory("together/meta-llama/Meta-Llama-3.1-8B", together_key).build()
    client = LLMFactory("mistral/mistral-large-latest", mistral_key).build()
    client = LLMFactory("deepseek/deepseek-chat", deepseek_key).build()

    # Local inference server
    client = LLMFactory("ollama/llama3.2", "ollama").build()
    client = LLMFactory("vllm/mistral-7b", "EMPTY").build(base_url="http://my-vllm:8000/v1")

    # Completely generic OpenAI-compatible endpoint
    client = LLMFactory("compatible/my-model", "sk-xxx").build(
        base_url="https://my-private-api.example.com/v1"
    )
"""

from __future__ import annotations

from typing import ClassVar, Optional, Any

from ravi.agents.llm import (
    EmbeddingClient,
    LLMClient,
    ModelProfile,
    get_model_profile,
    list_models,
)
from ravi.logger import setup_logging

logger = setup_logging()


# ── Provider detection ────────────────────────────────────────────────────────

_OPENAI_PREFIXES = ("gpt-", "o1-", "o3-", "o4-", "dall-e", "whisper", "tts-")
_ANTHROPIC_PREFIXES = ("claude-",)
_GEMINI_PREFIXES = ("gemini-",)

# All providers that use the OpenAI Chat Completions API.
# "openai" uses the Responses API instead; it is kept separate.
_CHAT_COMPLETIONS_PROVIDERS: frozenset[str] = frozenset(
    {
        "groq",
        "openrouter",
        "together",
        "fireworks",
        "perplexity",
        "mistral",
        "deepseek",
        "cerebras",
        "sambanova",
        "nvidia",
        "ollama",
        "lmstudio",
        "vllm",
        "compatible",  # generic catch-all — caller must pass base_url=
    }
)

_PROVIDER_PREFIXES: dict[str, str] = {
    # Cloud — proprietary APIs
    "openai": "openai",
    "anthropic": "anthropic",
    "gemini": "gemini",
    "google": "gemini",
    # Cloud — OpenAI-compatible
    "groq": "groq",
    "openrouter": "openrouter",
    "together": "together",
    "fireworks": "fireworks",
    "perplexity": "perplexity",
    "mistral": "mistral",
    "deepseek": "deepseek",
    "cerebras": "cerebras",
    "sambanova": "sambanova",
    "nvidia": "nvidia",
    # Local — OpenAI-compatible
    "ollama": "ollama",
    "lmstudio": "lmstudio",
    "vllm": "vllm",
    # Generic catch-all
    "compatible": "compatible",
}


def detect_provider(model: str) -> str:
    """Return the provider for *model* — one of the keys in ``_PROVIDER_PREFIXES``.

    Raises ``ValueError`` for unrecognised provider prefixes.
    """
    m = model.lower().strip()

    if "/" in m:
        prefix = m.split("/", 1)[0]
        provider = _PROVIDER_PREFIXES.get(prefix)
        if provider is None:
            raise ValueError(
                f"Unknown provider prefix {prefix!r} in model {model!r}. "
                f"Supported: {', '.join(_PROVIDER_PREFIXES)}"
            )
        return provider

    for p in _OPENAI_PREFIXES:
        if m.startswith(p):
            return "openai"
    for p in _ANTHROPIC_PREFIXES:
        if m.startswith(p):
            return "anthropic"
    for p in _GEMINI_PREFIXES:
        if m.startswith(p):
            return "gemini"

    logger.warning("Cannot detect provider for %r — defaulting to openai", model)
    return "openai"


def strip_provider_prefix(model: str) -> str:
    """Remove the leading ``provider/`` segment.

    ``openrouter/org/model`` → ``org/model`` (nested path preserved).
    """
    if "/" in model:
        return model.split("/", 1)[1]
    return model


# ── Factory class ─────────────────────────────────────────────────────────────


class LLMFactory:
    """Build and inspect an LLM client from a single *(model, api_key)* pair.

    The class is intentionally minimal — connection wiring only.
    Cost estimation and capability introspection go through :attr:`profile`.

    Example::

        factory = LLMFactory("gemini-2.5-flash", os.environ["GEMINI_API_KEY"])
        client  = factory.build(temperature=0.3)

        # Cost estimation before calling
        est = factory.estimate_cost(input_tokens=5_000, output_tokens=1_000)
        print(f"Estimated cost: ${est:.6f}")

        # Inspect model capabilities
        if factory.profile and factory.profile.supports_vision:
            ...
    """

    # Default base URLs for OpenAI-compatible providers.
    # "compatible" and "vllm" are intentionally absent — callers must pass base_url=.
    _BASE_URLS: ClassVar[dict[str, str]] = {
        # Cloud
        "groq": "https://api.groq.com/openai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "together": "https://api.together.xyz/v1",
        "fireworks": "https://api.fireworks.ai/inference/v1",
        "perplexity": "https://api.perplexity.ai",
        "mistral": "https://api.mistral.ai/v1",
        "deepseek": "https://api.deepseek.com/v1",
        "cerebras": "https://api.cerebras.ai/v1",
        "sambanova": "https://api.sambanova.ai/v1",
        "nvidia": "https://integrate.api.nvidia.com/v1",
        # Local defaults (user can override with base_url=)
        "ollama": "http://localhost:11434/v1",
        "lmstudio": "http://localhost:1234/v1",
        "vllm": "http://localhost:8000/v1",
    }

    def __init__(self, model: str, api_key: str) -> None:
        """
        Args:
            model:   Model identifier — with or without provider prefix.
                     Examples: ``"gpt-4o"``, ``"anthropic/claude-sonnet-4-20250514"``,
                     ``"groq/llama-3.3-70b-versatile"``.
            api_key: API key for the detected provider.
        """
        if not model.strip():
            raise ValueError("model must not be empty")
        if not api_key.strip():
            raise ValueError("api_key must not be empty")

        self._raw_model = model.strip()
        self._api_key = api_key.strip()
        self._provider = detect_provider(self._raw_model)
        self._bare_model = strip_provider_prefix(self._raw_model)
        self._profile: Optional[ModelProfile] = get_model_profile(self._bare_model)

        if self._profile is None:
            logger.warning(
                "No profile found for model %r — cost estimation will return 0.0.",
                self._bare_model,
            )

    # ── Read-only properties ──────────────────────────────────────────────────

    @property
    def model(self) -> str:
        """Original model string as passed to the constructor."""
        return self._raw_model

    @property
    def provider(self) -> str:
        """Detected provider: ``"openai"``, ``"anthropic"``, ``"gemini"``,
        ``"groq"``, or ``"openrouter"``."""
        return self._provider

    @property
    def bare_model(self) -> str:
        """Model ID with the provider prefix stripped (used in API calls)."""
        return self._bare_model

    @property
    def profile(self) -> Optional[ModelProfile]:
        """Model profile from the registry, or ``None`` for unknown models."""
        return self._profile

    # ── Core factory method ───────────────────────────────────────────────────

    def build(
        self,
        **kwargs: Any,
    ) -> LLMClient:
        """Create and return the configured :class:`LLMClient`.

        Args:
            **kwargs: Extra parameters passed to the client constructor
                (e.g. max_tokens, stop_sequences).
        """
        # Caller-supplied base_url takes precedence over the provider default.
        base_url = kwargs.get("base_url") or self._BASE_URLS.get(self._provider)
        kwargs["base_url"] = base_url

        if self._provider == "compatible" and not base_url:
            raise ValueError(
                "provider 'compatible' requires an explicit base_url= kwarg. "
                "Example: LLMFactory('compatible/my-model', key).build(base_url='http://...')"
            )

        if self._provider == "openai":
            from ravi.integrations.llm.openai.openai_client import OpenAIClient

            return OpenAIClient(
                model=self._bare_model,
                api_key=self._api_key,
                **kwargs,
            )

        if self._provider in _CHAT_COMPLETIONS_PROVIDERS:
            from ravi.capabilities.llm.chat_client import (
                OpenAIChatCompletionClient,
            )

            client = OpenAIChatCompletionClient(
                model=self._bare_model,
                api_key=self._api_key,
                **kwargs,
            )
            # Tag the client so tool-schema strict-mode logic can skip strict=true
            # for providers that don't support it.
            client.provider = self._provider  # type: ignore[attr-defined]
            return client

        if self._provider == "anthropic":
            from ravi.integrations.llm.anthropic.anthropic_client import AnthropicClient

            return AnthropicClient(
                model=self._bare_model,
                api_key=self._api_key,
                **kwargs,
            )

        if self._provider == "gemini":
            from ravi.integrations.llm.gemini.gemini_client import GeminiClient

            return GeminiClient(
                model=self._bare_model,
                api_key=self._api_key,
                **kwargs,
            )

        raise ValueError(f"Unsupported provider: {self._provider!r}")

    # ── Cost estimation ───────────────────────────────────────────────────────

    def estimate_cost(self, *, input_tokens: int, output_tokens: int) -> float:
        """Return the estimated cost in USD for one request.

        Returns ``0.0`` if the model is not in the registry.
        """
        if not self._profile:
            return 0.0
        return (
            self._profile.input_cost_per_mtok * input_tokens / 1_000_000
            + self._profile.output_cost_per_mtok * output_tokens / 1_000_000
        )

    # ── Static helpers ────────────────────────────────────────────────────────

    @staticmethod
    def models(provider: Optional[str] = None) -> list[ModelProfile]:
        """Return all profiles in the registry, optionally filtered by provider."""
        return list_models(provider)

    @staticmethod
    def profile_for(model: str) -> Optional[ModelProfile]:
        """Look up a profile by model name or alias."""
        return get_model_profile(strip_provider_prefix(model))

    def __repr__(self) -> str:
        cost = (
            f"${self._profile.input_cost_per_mtok}/${self._profile.output_cost_per_mtok} /MTok"
            if self._profile
            else "unknown cost"
        )
        return f"LLMFactory(model={self._bare_model!r}, provider={self._provider!r}, {cost})"


# ── Server-layer helpers (multi-key resolution for lifespan/route wiring) ────
#
# The server pulls multiple provider keys from settings and picks the right one
# at runtime based on which providers are configured.  These helpers implement
# that logic on top of LLMFactory.

CHAT_MODEL_FALLBACKS: tuple[str, ...] = (
    "openai/gpt-5.4-mini",
    "google/gemini-2.5-flash",
    "groq/llama-3.3-70b-versatile",
    "openrouter/liquid/lfm-2.5-1.2b-thinking:free",
    "anthropic/claude-sonnet-4-20250514",
)

VISION_MODEL_FALLBACKS: tuple[str, ...] = (
    "google/gemini-2.5-flash",
    "openrouter/openai/gpt-4o-mini",
    "openai/gpt-4o-mini",
    "anthropic/claude-3-5-sonnet-20241022",
)


def has_provider_api_key(
    provider: str,
    api_keys: Optional[dict[str, str]] = None,
) -> bool:
    """Return True when *api_keys* contains a non-empty key for *provider*."""
    keys = api_keys or {}
    lookup = "google" if provider == "gemini" else provider
    return bool((keys.get(provider) or keys.get(lookup) or "").strip())


def _pick_api_key(provider: str, api_keys: dict[str, str]) -> Optional[str]:
    lookup = "google" if provider == "gemini" else provider
    return (api_keys.get(provider) or api_keys.get(lookup)) or None


def resolve_model_for_available_credentials(
    model: str,
    *,
    api_keys: Optional[dict[str, str]] = None,
    fallback_models: Optional[list[str] | tuple[str, ...]] = None,
) -> str:
    """Return the first model whose provider has a configured key in *api_keys*."""
    candidates: list[str] = []
    seen: set[str] = set()
    for candidate in (model, *(fallback_models or ())):
        n = candidate.strip()
        if n and n not in seen:
            candidates.append(n)
            seen.add(n)
    for candidate in candidates:
        if has_provider_api_key(detect_provider(candidate), api_keys):
            return candidate
    return model


def resolve_vision_model_for_available_credentials(
    model: str,
    *,
    api_keys: Optional[dict[str, str]] = None,
    fallback_models: Optional[list[str] | tuple[str, ...]] = None,
) -> str:
    """Resolve to a credentialed model that supports vision."""
    resolved = resolve_model_for_available_credentials(
        model, api_keys=api_keys, fallback_models=fallback_models
    )
    if model_supports_vision(resolved):
        return resolved

    candidates: list[str] = []
    seen: set[str] = set()
    for candidate in (
        resolved,
        model,
        *(fallback_models or ()),
        *VISION_MODEL_FALLBACKS,
    ):
        n = candidate.strip()
        if n and n not in seen:
            candidates.append(n)
            seen.add(n)
    for candidate in candidates:
        if not has_provider_api_key(detect_provider(candidate), api_keys):
            continue
        if model_supports_vision(candidate):
            return candidate
    return resolved


def build_client(
    model: str,
    api_key: Optional[str] = None,
    temperature: float = 0.7,
    **kwargs: Any,
) -> LLMClient:
    """Convenience: one-liner to build an LLMClient."""
    factory = LLMFactory(model=model, api_key=api_key or "sk-placeholder")
    return factory.build(temperature=temperature, **kwargs)


def create_model_client(
    model: str,
    *,
    api_keys: Optional[dict[str, str]] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
    # OpenAI-compatible base URL overrides
    openai_base_url: Optional[str] = None,
    groq_base_url: Optional[str] = None,
    openrouter_base_url: Optional[str] = None,
    openrouter_site_url: Optional[str] = None,
    openrouter_app_name: Optional[str] = None,
    **_kwargs: object,
) -> LLMClient:
    """Server helper: create a client from a multi-provider *api_keys* dict.

    Prefer ``LLMFactory(model, api_key).build()`` for direct use.
    This helper exists for server lifespan wiring where keys come from settings.
    """
    keys = api_keys or {}
    provider = detect_provider(model)
    api_key = _pick_api_key(provider, keys) or ""

    base_url: Optional[str] = None
    if provider == "openai":
        base_url = openai_base_url
    elif provider == "groq":
        base_url = groq_base_url or LLMFactory._BASE_URLS["groq"]
    elif provider == "openrouter":
        base_url = openrouter_base_url or LLMFactory._BASE_URLS["openrouter"]
    elif provider in _CHAT_COMPLETIONS_PROVIDERS:
        base_url = LLMFactory._BASE_URLS.get(provider)

    if provider == "openrouter" and (openrouter_site_url or openrouter_app_name):
        from ravi.capabilities.llm.chat_client import (
            OpenAIChatCompletionClient,
        )

        bare = strip_provider_prefix(model)
        extra_headers = {
            k: v
            for k, v in {
                "HTTP-Referer": openrouter_site_url,
                "X-Title": openrouter_app_name,
            }.items()
            if v
        }
        client = OpenAIChatCompletionClient(
            model=bare,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
            base_url=base_url,
            extra_headers=extra_headers or None,
        )
        setattr(client, "provider", provider)
        return client

    client = LLMFactory(model, api_key or "sk-placeholder").build(
        temperature=temperature,
        max_tokens=max_tokens,
        base_url=base_url,
    )
    setattr(client, "provider", provider)
    return client


# ── Embedding factory (kept separate — different key, different client) ────────


def create_embedding_client(
    model: str = "text-embedding-3-small",
    *,
    api_key: Optional[str] = None,
    api_keys: Optional[dict[str, str]] = None,
    dimensions: Optional[int] = None,
    base_url: Optional[str] = None,
) -> EmbeddingClient:
    """Create an embedding client.

    Pass either ``api_key`` (direct) or ``api_keys`` dict (server multi-provider path).
    """
    provider = detect_embedding_provider(model)
    bare = strip_provider_prefix(model)
    resolved_key = api_key or _pick_api_key(provider, api_keys or {})

    if provider == "openai":
        from ravi.integrations.llm.openai.openai_embedding_client import (
            OpenAIEmbeddingClient,
        )

        return OpenAIEmbeddingClient(
            model=bare,
            api_key=resolved_key,
            dimensions=dimensions,
            base_url=base_url,
        )

    if provider == "gemini":
        from ravi.integrations.llm.gemini.gemini_embedding_client import (
            GeminiEmbeddingClient,
        )

        return GeminiEmbeddingClient(
            model=bare,
            api_key=resolved_key,
            dimensions=dimensions,
        )

    if provider == "sentence_transformers":
        from ravi.capabilities.llm.sentence_transformers_embedding_client import (
            SentenceTransformersEmbeddingClient,
        )

        return SentenceTransformersEmbeddingClient(bare, batch_size=64)

    raise ValueError(f"Unsupported embedding provider: {provider!r}")


# ── Embedding provider detection ──────────────────────────────────────────────


def detect_embedding_provider(model: str) -> str:
    """Detect the embedding provider — ``"openai"``, ``"gemini"``, or ``"sentence_transformers"``."""
    m = model.lower().strip()

    if "/" in m:
        prefix = m.split("/", 1)[0]
        if prefix in ("openai",):
            return "openai"
        if prefix in ("gemini", "google"):
            return "gemini"
        if prefix in ("sentence-transformers",):
            return "sentence_transformers"
        logger.warning(
            "Unknown embedding provider prefix %r — defaulting to openai", prefix
        )
        return "openai"

    if m == "text-embedding-004":
        return "gemini"
    if m.startswith("text-embedding-"):
        return "openai"
    if m.startswith("embedding-"):
        return "gemini"

    logger.warning(
        "Cannot detect embedding provider for %r — defaulting to openai", model
    )
    return "openai"


# ── Convenience helpers (used by server/lifespan wiring) ─────────────────────


def model_supports_vision(model: str) -> bool:
    """Return ``True`` when the model is known to accept image inputs."""
    profile = LLMFactory.profile_for(model)
    return bool(profile and profile.supports_vision)
