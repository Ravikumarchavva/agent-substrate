"""Model metadata registry — context lengths, costs, capabilities.

Provides ``ModelProfile`` and a pre-populated ``MODEL_REGISTRY`` so the
framework (and users) can query any model's capabilities:

    from ravi.agents.llm.models import get_model_profile, estimate_cost

    profile = get_model_profile("claude-sonnet-4-20250514")
    assert profile.context_length == 200_000
    assert profile.supports_thinking is True

    cost = estimate_cost("gpt-4o", input_tokens=1000, output_tokens=500)
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelProfile:
    """Metadata about a specific LLM model."""

    name: str
    provider: str  # "openai" | "anthropic" | "gemini"
    context_length: int  # max input tokens
    max_output_tokens: int  # max output tokens

    # Cost in USD per 1 million tokens
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0

    # Capabilities
    supports_vision: bool = False
    supports_tools: bool = True
    supports_structured_output: bool = True
    supports_streaming: bool = True
    supports_thinking: bool = False
    thinking_always_on: bool = False  # o-series models
    supports_audio_input: bool = False
    supports_audio_output: bool = False
    supports_image_generation: bool = False
    supports_prompt_caching: bool = False

    modalities: tuple[str, ...] = ("text",)
    default_dimensions: int | None = None
    aliases: tuple[str, ...] = ()


_MODELS: list[ModelProfile] = [
    # ── OpenAI ────────────────────────────────────────────────────────────────
    ModelProfile(
        name="gpt-4o",
        provider="openai",
        context_length=128_000,
        max_output_tokens=16_384,
        input_cost_per_mtok=2.50,
        output_cost_per_mtok=10.00,
        supports_vision=True,
        supports_audio_input=True,
        supports_audio_output=True,
        modalities=("text", "image", "audio"),
        aliases=("gpt-4o-2024-11-20",),
    ),
    ModelProfile(
        name="gpt-4o-mini",
        provider="openai",
        context_length=128_000,
        max_output_tokens=16_384,
        input_cost_per_mtok=0.15,
        output_cost_per_mtok=0.60,
        supports_vision=True,
        modalities=("text", "image"),
        aliases=("gpt-4o-mini-2024-07-18",),
    ),
    ModelProfile(
        name="gpt-4.1",
        provider="openai",
        context_length=1_047_576,
        max_output_tokens=32_768,
        input_cost_per_mtok=2.00,
        output_cost_per_mtok=8.00,
        supports_vision=True,
        modalities=("text", "image"),
    ),
    ModelProfile(
        name="gpt-4.1-mini",
        provider="openai",
        context_length=1_047_576,
        max_output_tokens=32_768,
        input_cost_per_mtok=0.40,
        output_cost_per_mtok=1.60,
        supports_vision=True,
        modalities=("text", "image"),
    ),
    ModelProfile(
        name="gpt-4.1-nano",
        provider="openai",
        context_length=1_047_576,
        max_output_tokens=32_768,
        input_cost_per_mtok=0.10,
        output_cost_per_mtok=0.40,
        supports_vision=True,
        modalities=("text", "image"),
    ),
    ModelProfile(
        name="o3",
        provider="openai",
        context_length=200_000,
        max_output_tokens=100_000,
        input_cost_per_mtok=10.00,
        output_cost_per_mtok=40.00,
        supports_vision=True,
        supports_thinking=True,
        thinking_always_on=True,
        modalities=("text", "image"),
        aliases=("o3-2025-04-16",),
    ),
    ModelProfile(
        name="o3-mini",
        provider="openai",
        context_length=200_000,
        max_output_tokens=100_000,
        input_cost_per_mtok=1.10,
        output_cost_per_mtok=4.40,
        supports_thinking=True,
        thinking_always_on=True,
        modalities=("text",),
        aliases=("o3-mini-2025-01-31",),
    ),
    ModelProfile(
        name="o4-mini",
        provider="openai",
        context_length=200_000,
        max_output_tokens=100_000,
        input_cost_per_mtok=1.10,
        output_cost_per_mtok=4.40,
        supports_vision=True,
        supports_thinking=True,
        thinking_always_on=True,
        modalities=("text", "image"),
        aliases=("o4-mini-2025-04-16",),
    ),
    ModelProfile(
        name="gpt-5",
        provider="openai",
        context_length=1_047_576,
        max_output_tokens=32_768,
        input_cost_per_mtok=2.00,
        output_cost_per_mtok=8.00,
        supports_vision=True,
        supports_audio_input=True,
        supports_audio_output=True,
        modalities=("text", "image", "audio"),
    ),
    ModelProfile(
        name="gpt-5-mini",
        provider="openai",
        context_length=1_047_576,
        max_output_tokens=32_768,
        input_cost_per_mtok=0.25,
        output_cost_per_mtok=2.00,
        supports_vision=True,
        modalities=("text", "image"),
        aliases=("gpt-5.4-mini",),
    ),
    # ── Anthropic ─────────────────────────────────────────────────────────────
    ModelProfile(
        name="claude-sonnet-4-20250514",
        provider="anthropic",
        context_length=200_000,
        max_output_tokens=16_384,
        input_cost_per_mtok=3.00,
        output_cost_per_mtok=15.00,
        supports_vision=True,
        supports_thinking=True,
        supports_prompt_caching=True,
        modalities=("text", "image"),
        aliases=("claude-sonnet-4",),
    ),
    ModelProfile(
        name="claude-opus-4-20250514",
        provider="anthropic",
        context_length=200_000,
        max_output_tokens=32_000,
        input_cost_per_mtok=15.00,
        output_cost_per_mtok=75.00,
        supports_vision=True,
        supports_thinking=True,
        supports_prompt_caching=True,
        modalities=("text", "image"),
        aliases=("claude-opus-4",),
    ),
    ModelProfile(
        name="claude-haiku-4-20250514",
        provider="anthropic",
        context_length=200_000,
        max_output_tokens=8_192,
        input_cost_per_mtok=1.00,
        output_cost_per_mtok=5.00,
        supports_vision=True,
        supports_thinking=True,
        supports_prompt_caching=True,
        modalities=("text", "image"),
        aliases=("claude-haiku-4",),
    ),
    ModelProfile(
        name="claude-3-5-sonnet-20241022",
        provider="anthropic",
        context_length=200_000,
        max_output_tokens=8_192,
        input_cost_per_mtok=3.00,
        output_cost_per_mtok=15.00,
        supports_vision=True,
        supports_prompt_caching=True,
        modalities=("text", "image"),
        aliases=("claude-3-5-sonnet", "claude-3-5-sonnet-latest"),
    ),
    ModelProfile(
        name="claude-3-5-haiku-20241022",
        provider="anthropic",
        context_length=200_000,
        max_output_tokens=8_192,
        input_cost_per_mtok=0.80,
        output_cost_per_mtok=4.00,
        supports_vision=True,
        supports_prompt_caching=True,
        modalities=("text", "image"),
        aliases=("claude-3-5-haiku", "claude-3-5-haiku-latest"),
    ),
    ModelProfile(
        name="claude-3-haiku-20240307",
        provider="anthropic",
        context_length=200_000,
        max_output_tokens=4_096,
        input_cost_per_mtok=0.25,
        output_cost_per_mtok=1.25,
        supports_vision=True,
        supports_prompt_caching=True,
        modalities=("text", "image"),
        aliases=("claude-3-haiku",),
    ),
    # ── Google Gemini ─────────────────────────────────────────────────────────
    ModelProfile(
        name="gemini-2.5-flash",
        provider="gemini",
        context_length=1_048_576,
        max_output_tokens=65_536,
        input_cost_per_mtok=0.30,
        output_cost_per_mtok=2.50,
        supports_vision=True,
        supports_thinking=True,
        supports_audio_input=True,
        supports_prompt_caching=True,
        modalities=("text", "image", "audio", "video"),
        aliases=("gemini-2.5-flash-preview-05-20", "gemini-2.5-flash-latest"),
    ),
    ModelProfile(
        name="gemini-2.5-pro",
        provider="gemini",
        context_length=1_048_576,
        max_output_tokens=65_536,
        input_cost_per_mtok=1.25,
        output_cost_per_mtok=10.00,
        supports_vision=True,
        supports_thinking=True,
        supports_audio_input=True,
        supports_prompt_caching=True,
        modalities=("text", "image", "audio", "video"),
        aliases=("gemini-2.5-pro-preview-06-05", "gemini-2.5-pro-latest"),
    ),
    ModelProfile(
        name="gemini-2.0-flash",
        provider="gemini",
        context_length=1_048_576,
        max_output_tokens=8_192,
        input_cost_per_mtok=0.10,
        output_cost_per_mtok=0.40,
        supports_vision=True,
        supports_audio_input=True,
        supports_image_generation=True,
        supports_prompt_caching=True,
        modalities=("text", "image", "audio", "video"),
        aliases=("gemini-2.0-flash-latest",),
    ),
    ModelProfile(
        name="gemini-1.5-flash",
        provider="gemini",
        context_length=1_048_576,
        max_output_tokens=8_192,
        input_cost_per_mtok=0.075,
        output_cost_per_mtok=0.30,
        supports_vision=True,
        supports_audio_input=True,
        supports_prompt_caching=True,
        modalities=("text", "image", "audio", "video"),
        aliases=("gemini-1.5-flash-latest", "gemini-1.5-flash-8b"),
    ),
    ModelProfile(
        name="gemini-1.5-pro",
        provider="gemini",
        context_length=2_097_152,
        max_output_tokens=8_192,
        input_cost_per_mtok=1.25,
        output_cost_per_mtok=5.00,
        supports_vision=True,
        supports_audio_input=True,
        supports_prompt_caching=True,
        modalities=("text", "image", "audio", "video"),
        aliases=("gemini-1.5-pro-latest",),
    ),
    # ── Groq ──────────────────────────────────────────────────────────────────
    ModelProfile(
        name="llama-3.3-70b-versatile",
        provider="groq",
        context_length=128_000,
        max_output_tokens=32_768,
        input_cost_per_mtok=0.59,
        output_cost_per_mtok=0.79,
        supports_vision=False,
        supports_tools=True,
        supports_streaming=True,
        modalities=("text",),
        aliases=("groq/llama-3.3-70b-versatile",),
    ),
    ModelProfile(
        name="llama-3.1-70b-versatile",
        provider="groq",
        context_length=128_000,
        max_output_tokens=32_768,
        input_cost_per_mtok=0.59,
        output_cost_per_mtok=0.79,
        supports_vision=False,
        supports_tools=True,
        supports_streaming=True,
        modalities=("text",),
        aliases=("groq/llama-3.1-70b-versatile",),
    ),
    ModelProfile(
        name="llama-3.1-8b-instant",
        provider="groq",
        context_length=128_000,
        max_output_tokens=8_192,
        input_cost_per_mtok=0.05,
        output_cost_per_mtok=0.08,
        supports_vision=False,
        supports_tools=True,
        supports_streaming=True,
        modalities=("text",),
        aliases=("groq/llama-3.1-8b-instant",),
    ),
    ModelProfile(
        name="llama3-70b-8192",
        provider="groq",
        context_length=8_192,
        max_output_tokens=8_192,
        input_cost_per_mtok=0.59,
        output_cost_per_mtok=0.79,
        supports_vision=False,
        supports_tools=True,
        supports_streaming=True,
        modalities=("text",),
        aliases=("groq/llama3-70b-8192",),
    ),
    ModelProfile(
        name="llama3-8b-8192",
        provider="groq",
        context_length=8_192,
        max_output_tokens=8_192,
        input_cost_per_mtok=0.05,
        output_cost_per_mtok=0.08,
        supports_vision=False,
        supports_tools=True,
        supports_streaming=True,
        modalities=("text",),
        aliases=("groq/llama3-8b-8192",),
    ),
    # ── Embedding Models ──────────────────────────────────────────────────────
    ModelProfile(
        name="text-embedding-3-small",
        provider="openai",
        context_length=8_191,
        max_output_tokens=0,
        input_cost_per_mtok=0.02,
        output_cost_per_mtok=0.0,
        supports_vision=False,
        supports_tools=False,
        supports_structured_output=False,
        supports_streaming=False,
        default_dimensions=1_536,
        modalities=("text",),
    ),
    ModelProfile(
        name="text-embedding-3-large",
        provider="openai",
        context_length=8_191,
        max_output_tokens=0,
        input_cost_per_mtok=0.13,
        output_cost_per_mtok=0.0,
        supports_vision=False,
        supports_tools=False,
        supports_structured_output=False,
        supports_streaming=False,
        default_dimensions=3_072,
        modalities=("text",),
    ),
    ModelProfile(
        name="text-embedding-ada-002",
        provider="openai",
        context_length=8_191,
        max_output_tokens=0,
        input_cost_per_mtok=0.10,
        output_cost_per_mtok=0.0,
        supports_vision=False,
        supports_tools=False,
        supports_structured_output=False,
        supports_streaming=False,
        default_dimensions=1_536,
        modalities=("text",),
        aliases=("ada-002",),
    ),
    ModelProfile(
        name="text-embedding-004",
        provider="gemini",
        context_length=2_048,
        max_output_tokens=0,
        input_cost_per_mtok=0.00625,
        output_cost_per_mtok=0.0,
        supports_vision=False,
        supports_tools=False,
        supports_structured_output=False,
        supports_streaming=False,
        default_dimensions=768,
        modalities=("text",),
    ),
]


def _build_registry() -> dict[str, ModelProfile]:
    registry: dict[str, ModelProfile] = {}
    for m in _MODELS:
        registry[m.name] = m
        for alias in m.aliases:
            registry[alias] = m
    return registry


MODEL_REGISTRY: dict[str, ModelProfile] = _build_registry()


def get_model_profile(model: str) -> ModelProfile | None:
    """Look up a model's profile by name or alias."""
    return MODEL_REGISTRY.get(model)


def get_context_length(model: str, default: int = 128_000) -> int:
    """Return the context length for a model, or *default* if unknown."""
    profile = get_model_profile(model)
    return profile.context_length if profile else default


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate the cost in USD for a request."""
    profile = get_model_profile(model)
    if not profile:
        return 0.0
    return (
        profile.input_cost_per_mtok * input_tokens / 1_000_000
        + profile.output_cost_per_mtok * output_tokens / 1_000_000
    )


def list_models(provider: str | None = None) -> list[ModelProfile]:
    """Return all registered models, optionally filtered by provider."""
    if provider:
        return [m for m in _MODELS if m.provider == provider]
    return list(_MODELS)
