from __future__ import annotations

from types import SimpleNamespace

import pytest

from google.genai import types as genai_types

from raavan.integrations.llm.factory import (
    create_model_client,
    detect_provider,
    model_supports_vision,
    resolve_vision_model_for_available_credentials,
    strip_provider_prefix,
)
from raavan.integrations.llm.gemini.gemini_client import GeminiClient


def test_detect_provider_openrouter() -> None:
    assert (
        detect_provider("openrouter/liquid/lfm-2.5-1.2b-thinking:free") == "openrouter"
    )


def test_detect_provider_groq() -> None:
    assert detect_provider("groq/llama-3.3-70b-versatile") == "groq"


def test_strip_provider_prefix_preserves_openrouter_target() -> None:
    assert (
        strip_provider_prefix("openrouter/liquid/lfm-2.5-1.2b-thinking:free")
        == "liquid/lfm-2.5-1.2b-thinking:free"
    )


def test_strip_provider_prefix_preserves_groq_target() -> None:
    assert (
        strip_provider_prefix("groq/llama-3.3-70b-versatile")
        == "llama-3.3-70b-versatile"
    )


def test_create_model_client_openrouter_uses_openrouter_config() -> None:
    client = create_model_client(
        "openrouter/liquid/lfm-2.5-1.2b-thinking:free",
        api_keys={"openrouter": "test-key"},
        openrouter_base_url="https://openrouter.ai/api/v1",
        openrouter_site_url="http://localhost:3000",
        openrouter_app_name="Raavan UI",
    )

    assert getattr(client, "provider", None) == "openrouter"
    assert client.model == "liquid/lfm-2.5-1.2b-thinking:free"
    assert getattr(client, "base_url", None) == "https://openrouter.ai/api/v1"


def test_create_model_client_groq_uses_groq_config() -> None:
    client = create_model_client(
        "groq/llama-3.3-70b-versatile",
        api_keys={"groq": "test-key"},
        groq_base_url="https://api.groq.com/openai/v1",
    )

    assert getattr(client, "provider", None) == "groq"
    assert client.model == "llama-3.3-70b-versatile"
    assert getattr(client, "base_url", None) == "https://api.groq.com/openai/v1"


def test_model_supports_vision_for_openrouter_routed_openai_model() -> None:
    assert model_supports_vision("openrouter/openai/gpt-4o-mini") is True


def test_resolve_vision_model_falls_back_from_text_only_model() -> None:
    resolved = resolve_vision_model_for_available_credentials(
        "groq/llama-3.3-70b-versatile",
        api_keys={"groq": "test-key", "google": "gemini-key"},
    )

    assert resolved == "google/gemini-2.5-flash"


def test_resolve_vision_model_uses_openrouter_when_only_openrouter_is_configured() -> (
    None
):
    resolved = resolve_vision_model_for_available_credentials(
        "groq/llama-3.3-70b-versatile",
        api_keys={"openrouter": "router-key"},
    )

    assert resolved == "openrouter/openai/gpt-4o-mini"


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
