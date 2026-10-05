"""Turning a recording into text, for the audio route and for recordings shared in a group."""

from __future__ import annotations

import logging
from typing import Any

from substrate.integrations.llm.factory import (
    create_model_client,
    detect_provider,
    strip_provider_prefix,
)
from substrate.integrations.llm.openai.openai_client import OpenAIClient
from substrate.integrations.tts.kokoro_client import get_kokoro_client
from substrate_cloud.shared.settings import settings

logger = logging.getLogger(__name__)


class TranscriptionUnavailable(Exception):
    """The recording cannot be transcribed here; ``status`` is the HTTP status that says so."""

    def __init__(self, detail: str, status: int = 501) -> None:
        super().__init__(detail)
        self.status = status


def resolve_model_client(
    app_state: Any,
    requested_model: str | None,
    fallback_model: str,
) -> tuple[Any, str, str]:
    """Resolve the correct provider client for an incoming audio request.

    Resolve the provider client without imposing an audio capability here.
    Individual endpoints validate the capabilities they support.
    """
    effective_model = (
        requested_model.strip()
        if requested_model and requested_model.strip()
        else fallback_model
    )
    if effective_model.startswith("local/kokoro"):
        return get_kokoro_client(), "local", effective_model
    default_client: Any = app_state.model_client
    effective_provider = detect_provider(effective_model)
    bare_model = strip_provider_prefix(effective_model)

    if (
        getattr(default_client, "provider", None) == effective_provider
        and getattr(default_client, "model", None) == bare_model
    ):
        client = default_client
    else:
        client = create_model_client(
            effective_model,
            api_keys=getattr(app_state, "api_keys", {}),
            **getattr(app_state, "model_client_kwargs", {}),
        )

    return client, effective_provider, bare_model


async def transcribe(
    app_state: Any,
    raw: bytes,
    filename: str,
    *,
    model: str = "",
    language: str | None = None,
    prompt: str | None = None,
) -> str:
    """The text of a recording. Only OpenAI models transcribe; anything else raises ``TranscriptionUnavailable``."""
    client, provider, effective_model = resolve_model_client(app_state, model, settings.STT_MODEL)
    if not isinstance(client, OpenAIClient):
        raise TranscriptionUnavailable(f"Transcription is only supported for OpenAI models, not '{provider}'")
    if provider == "openrouter":
        raise TranscriptionUnavailable("OpenRouter chat models are not supported for transcription")
    try:
        return await client.transcribe(
            audio_bytes=raw, filename=filename, model=effective_model, language=language or None, prompt=prompt or None
        )
    except NotImplementedError as exc:
        raise TranscriptionUnavailable(str(exc)) from exc
    except Exception as exc:
        logger.exception("Transcription failed")
        raise TranscriptionUnavailable(str(exc), status=502) from exc
