from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from loamz.agents.bootstrap.container import Container
from loamz.agents.bootstrap.settings import AudioSettings
from loamz.agents.features.conversations.audio.model import RealtimeTokenResponse, TTSRequest
from loamz.data.storage.errors import InvalidRequestError, ServiceUnavailableError

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 25 * 1024 * 1024
"""OpenAI's hard limit for an uploaded recording."""

CONTENT_TYPES = {
    "mp3": "audio/mpeg",
    "opus": "audio/ogg; codecs=opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
}


class AudioUnavailable(ServiceUnavailableError):
    """The configured provider cannot do this (not an outage): the client should pick another model."""


@dataclass
class Speech:
    chunks: AsyncIterator[bytes]
    content_type: str


def tts_error(exc: Exception) -> str:
    """A provider failure in words a user can act on."""
    text = str(exc)
    if "429" in text or "RESOURCE_EXHAUSTED" in text:
        return (
            "The speech model's quota is used up. Try again later or choose another speech model."
        )
    if "404" in text or "NOT_FOUND" in text:
        return "That speech model is not available. Choose another in Settings."
    return "The speech model could not produce audio."


class AudioService:
    """Speech to text, text to speech and the realtime session, on whichever provider the model names."""

    def __init__(self, container: Container, settings: AudioSettings) -> None:
        self._container = container
        self._cfg = settings

    def client_for(self, requested: str | None, fallback: str) -> tuple[Any, str, str]:
        """`(client, provider, model)` for the requested model, else the configured default for this job."""
        from loamz.agents.providers.llm import detect_provider, strip_provider_prefix

        effective = requested.strip() if requested and requested.strip() else fallback
        if effective.startswith("local/kokoro"):
            from loamz.agents.providers.tts.kokoro_client import get_kokoro_client

            return get_kokoro_client(), "local", effective
        return (
            self._container.chat_model(effective),
            detect_provider(effective),
            strip_provider_prefix(effective),
        )

    async def transcribe(
        self,
        raw: bytes,
        filename: str,
        *,
        model: str = "",
        language: str | None = None,
        prompt: str | None = None,
    ) -> str:
        """The text of a recording. Only OpenAI models transcribe."""
        from loamz.agents.providers.llm import OpenAIClient

        if not raw:
            raise InvalidRequestError("Empty audio file")
        if len(raw) > MAX_AUDIO_BYTES:
            raise InvalidRequestError("Audio file exceeds 25 MB limit")
        client, provider, effective = self.client_for(model, self._cfg.stt_model)
        if not isinstance(client, OpenAIClient):
            raise AudioUnavailable(
                f"Transcription is only supported for OpenAI models, not '{provider}'"
            )
        if provider == "openrouter":
            raise AudioUnavailable("OpenRouter chat models are not supported for transcription")
        try:
            return await client.transcribe(
                audio_bytes=raw,
                filename=filename,
                model=effective,
                language=language or None,
                prompt=prompt or None,
            )
        except NotImplementedError as exc:
            raise AudioUnavailable(str(exc)) from exc
        except Exception as exc:
            logger.exception("Transcription failed")
            raise ServiceUnavailableError(str(exc)) from exc

    async def speak(self, body: TTSRequest) -> Speech:
        """Text to speech as an audio stream. The first chunk is pulled before answering, so a provider failure is an
        error the client can show rather than a 200 whose body stops short."""
        from loamz.agents.providers.llm import OpenAIClient
        from loamz.agents.providers.llm.gemini.gemini_client import GeminiClient
        from loamz.agents.providers.tts.kokoro_client import KokoroTTSClient

        client, provider, effective = self.client_for(body.model, self._cfg.tts_model)
        if not isinstance(client, OpenAIClient | GeminiClient | KokoroTTSClient):
            raise AudioUnavailable(f"Text-to-speech is not supported for provider '{provider}'")
        if provider == "openrouter":
            raise AudioUnavailable("OpenRouter chat models are not supported for speech synthesis")
        local = isinstance(client, GeminiClient | KokoroTTSClient)
        fmt = "wav" if local else (body.response_format or "mp3")
        if body.voice and body.voice.strip():
            voice = body.voice.strip()
        elif isinstance(client, KokoroTTSClient):
            voice = "af_heart"
        elif isinstance(client, GeminiClient):
            voice = self._cfg.tts_voice
        else:
            voice = "coral"
        audio = client.stream_tts(
            text=body.text,
            voice=voice,
            model=effective,
            response_format=fmt,
            instructions=body.instructions,
        )
        try:
            first = await anext(audio)
        except StopAsyncIteration:
            raise ServiceUnavailableError("No audio was produced") from None
        except NotImplementedError as exc:
            raise AudioUnavailable(str(exc)) from exc
        except Exception as exc:
            logger.exception("TTS failed")
            raise ServiceUnavailableError(tts_error(exc)) from exc

        async def chunks() -> AsyncIterator[bytes]:
            yield first
            async for chunk in audio:
                yield chunk

        return Speech(chunks(), CONTENT_TYPES.get(fmt, "audio/mpeg"))

    async def realtime_token(self, model: str = "", voice: str = "") -> RealtimeTokenResponse:
        """A short-lived ephemeral realtime session token, so the browser never holds the server's API key."""
        client, provider, effective = self.client_for(model, self._cfg.realtime_model)
        if provider != "openai":
            raise AudioUnavailable(
                "Realtime speech is currently available only with OpenAI realtime models"
            )
        if not client.supports_s2s:
            raise AudioUnavailable(
                "Speech-to-speech not supported by the configured audio provider"
            )
        try:
            session = await client.create_s2s_session(
                model=effective, voice=voice.strip() or self._cfg.realtime_voice, instructions=None
            )
        except NotImplementedError as exc:
            raise AudioUnavailable(str(exc)) from exc
        except Exception as exc:
            logger.exception("Failed to create S2S session")
            raise ServiceUnavailableError(str(exc)) from exc
        secret = session.get("client_secret", "")
        if isinstance(secret, dict):
            secret = secret.get("value", "")
        return RealtimeTokenResponse(
            client_secret=secret,
            expires_at=session.get("expires_at", 0),
            session_id=session.get("id", ""),
        )

    def realtime_url(self, requested_model: str | None) -> str | None:
        """The upstream websocket URL for the realtime proxy, or None if the provider has none."""
        client, provider, effective = self.client_for(requested_model, self._cfg.realtime_model)
        if provider != "openai":
            return None
        try:
            return client.s2s_ws_url(effective)
        except NotImplementedError:
            return None

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        return 0


def audio(container: Container) -> AudioService:
    return AudioService(container, container.settings.audio)
