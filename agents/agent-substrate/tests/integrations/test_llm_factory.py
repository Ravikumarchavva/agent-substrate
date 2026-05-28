from __future__ import annotations

from types import SimpleNamespace

import pytest

from google.genai import types as genai_types

from ravi.integrations.llm.factory import (
    LLMFactory,
    detect_provider,
    model_supports_vision,
    strip_provider_prefix,
)
from ravi.integrations.llm.gemini.gemini_client import GeminiClient


def test_detect_provider_openrouter() -> None:
    assert detect_provider("openrouter/liquid/lfm-2.5-1.2b-thinking:free") == "openrouter"


def test_detect_provider_groq() -> None:
    assert detect_provider("groq/llama-3.3-70b-versatile") == "groq"


def test_detect_provider_anthropic() -> None:
    assert detect_provider("claude-sonnet-4-20250514") == "anthropic"


def test_detect_provider_gemini() -> None:
    assert detect_provider("gemini-2.5-flash") == "gemini"


def test_detect_provider_openai() -> None:
    assert detect_provider("gpt-4o") == "openai"


def test_detect_provider_unknown_prefix_raises() -> None:
    with pytest.raises(ValueError, match="Unknown provider prefix"):
        detect_provider("unknown/some-model")


def test_strip_provider_prefix_preserves_openrouter_target() -> None:
    assert (
        strip_provider_prefix("openrouter/liquid/lfm-2.5-1.2b-thinking:free")
        == "liquid/lfm-2.5-1.2b-thinking:free"
    )


def test_strip_provider_prefix_preserves_groq_target() -> None:
    assert strip_provider_prefix("groq/llama-3.3-70b-versatile") == "llama-3.3-70b-versatile"


# ---------------------------------------------------------------------------
# LLMFactory construction
# ---------------------------------------------------------------------------


def test_factory_empty_model_raises() -> None:
    with pytest.raises(ValueError, match="model"):
        LLMFactory("", "sk-test")


def test_factory_empty_key_raises() -> None:
    with pytest.raises(ValueError, match="api_key"):
        LLMFactory("gpt-4o", "")


def test_factory_detects_provider() -> None:
    f = LLMFactory("claude-sonnet-4-20250514", "sk-ant-test")
    assert f.provider == "anthropic"
    assert f.bare_model == "claude-sonnet-4-20250514"


def test_factory_strips_prefix() -> None:
    f = LLMFactory("groq/llama-3.3-70b-versatile", "gsk-test")
    assert f.provider == "groq"
    assert f.bare_model == "llama-3.3-70b-versatile"


def test_factory_profile_known_model() -> None:
    f = LLMFactory("gpt-4o", "sk-test")
    assert f.profile is not None
    assert f.profile.provider == "openai"
    assert f.profile.supports_vision is True


def test_factory_profile_unknown_model_is_none() -> None:
    f = LLMFactory("gpt-99-turbo-ultra", "sk-test")
    assert f.profile is None


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------


def test_estimate_cost_known_model() -> None:
    f = LLMFactory("gpt-4o", "sk-test")
    # gpt-4o: $2.50/$10.00 per MTok
    cost = f.estimate_cost(input_tokens=1_000_000, output_tokens=1_000_000)
    assert abs(cost - 12.50) < 0.01


def test_estimate_cost_unknown_model_returns_zero() -> None:
    f = LLMFactory("gpt-99-turbo-ultra", "sk-test")
    assert f.estimate_cost(input_tokens=1_000, output_tokens=500) == 0.0


def test_estimate_cost_zero_tokens() -> None:
    f = LLMFactory("claude-sonnet-4-20250514", "sk-ant-test")
    assert f.estimate_cost(input_tokens=0, output_tokens=0) == 0.0


# ---------------------------------------------------------------------------
# Static helpers
# ---------------------------------------------------------------------------


def test_models_all() -> None:
    all_models = LLMFactory.models()
    assert len(all_models) > 10


def test_models_filtered_by_provider() -> None:
    anthropic = LLMFactory.models("anthropic")
    assert all(m.provider == "anthropic" for m in anthropic)
    assert len(anthropic) >= 3


def test_profile_for_alias() -> None:
    profile = LLMFactory.profile_for("claude-3-5-sonnet-latest")
    assert profile is not None
    assert profile.name == "claude-3-5-sonnet-20241022"


def test_profile_for_with_prefix() -> None:
    profile = LLMFactory.profile_for("anthropic/claude-sonnet-4-20250514")
    assert profile is not None
    assert profile.provider == "anthropic"


def test_model_supports_vision_true() -> None:
    assert model_supports_vision("gpt-4o") is True


def test_model_supports_vision_false() -> None:
    assert model_supports_vision("llama-3.3-70b-versatile") is False


# ---------------------------------------------------------------------------
# build() produces the right client type
# ---------------------------------------------------------------------------


def test_build_openrouter_uses_chat_client() -> None:
    f = LLMFactory("openrouter/liquid/lfm-2.5-1.2b-thinking:free", "or-test")
    client = f.build()
    assert client.model == "liquid/lfm-2.5-1.2b-thinking:free"
    assert getattr(client, "base_url", None) == "https://openrouter.ai/api/v1"


def test_build_groq_uses_groq_base_url() -> None:
    f = LLMFactory("groq/llama-3.3-70b-versatile", "gsk-test")
    client = f.build()
    assert client.model == "llama-3.3-70b-versatile"
    assert getattr(client, "base_url", None) == "https://api.groq.com/openai/v1"


def test_build_custom_base_url_override() -> None:
    f = LLMFactory("gpt-4o", "sk-test")
    client = f.build(base_url="http://localhost:8080/v1")
    assert getattr(client, "base_url", None) == "http://localhost:8080/v1"


def test_repr_contains_model_and_provider() -> None:
    f = LLMFactory("gpt-4o", "sk-test")
    r = repr(f)
    assert "gpt-4o" in r
    assert "openai" in r


# ---------------------------------------------------------------------------
# Gemini streaming TTS (kept from original test suite)
# ---------------------------------------------------------------------------


async def test_gemini_stream_tts_returns_audio_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = GeminiClient(model="gemini-3.1-flash-tts-preview", api_key="test-key")

    async def fake_generate_content(
        *, model: str, contents: list, config: genai_types.GenerateContentConfig
    ):
        assert model == "gemini-3.1-flash-tts-preview"
        assert contents[0].parts[0].text.endswith("Hello from Gemini")
        assert config.response_modalities == [genai_types.Modality.AUDIO]
        return SimpleNamespace(
            candidates=[
                SimpleNamespace(
                    content=SimpleNamespace(
                        parts=[
                            SimpleNamespace(
                                inline_data=SimpleNamespace(data=b"wav-bytes")
                            )
                        ]
                    )
                )
            ]
        )

    monkeypatch.setattr(
        client.client.aio.models, "generate_content", fake_generate_content
    )

    chunks = [
        chunk
        async for chunk in client.stream_tts(
            text="Hello from Gemini",
            voice="Kore",
            response_format="wav",
        )
    ]

    assert chunks == [b"wav-bytes"]
