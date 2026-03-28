"""OpenAI implementation of ``BaseAudioClient``.

All OpenAI SDK imports live here — no other part of the framework imports
``openai`` for audio purposes.  Swap this class out to use a different
provider without touching any route or service code.
"""

from __future__ import annotations

import io
import logging
from typing import AsyncIterator, Optional

from openai import AsyncOpenAI

from agent_framework.integrations.audio.base_audio_client import BaseAudioClient

logger = logging.getLogger(__name__)


class OpenAIAudioClient(BaseAudioClient):
    """Audio client backed by the OpenAI ``audio.*`` API.

    Args:
        api_key: OpenAI API key.  Defaults to the ``OPENAI_API_KEY``
                 environment variable when ``None``.
        default_stt_model:  Default speech-to-text model.
        default_tts_model:  Default text-to-speech model.
        default_voice:      Default TTS voice.
        default_tts_format: Default audio container format.
        realtime_model:     Default model for ``create_realtime_session``.
    """

    _REALTIME_UPSTREAM = "wss://api.openai.com/v1/realtime"

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        default_stt_model: str = "whisper-1",
        default_tts_model: str = "gpt-4o-mini-tts",
        default_voice: str = "coral",
        default_tts_format: str = "mp3",
        realtime_model: str = "gpt-4o-realtime-preview-2024-12-17",
    ) -> None:
        self._client = AsyncOpenAI(api_key=api_key)
        self._default_stt_model = default_stt_model
        self._default_tts_model = default_tts_model
        self._default_voice = default_voice
        self._default_tts_format = default_tts_format
        self._realtime_model = realtime_model

    # ── Transcription ─────────────────────────────────────────────────────────

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        model: str = "",
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> str:
        """Transcribe audio via Whisper / GPT-4o-transcribe."""
        effective_model = model or self._default_stt_model
        logger.info(
            "Transcribing audio: file=%s model=%s bytes=%d",
            filename,
            effective_model,
            len(audio_bytes),
        )

        kwargs: dict = {}
        if language:
            kwargs["language"] = language
        if prompt:
            kwargs["prompt"] = prompt

        file_tuple = (filename, io.BytesIO(audio_bytes), _mime_for(filename))

        result = await self._client.audio.transcriptions.create(
            model=effective_model,
            file=file_tuple,
            response_format="text",
            **kwargs,
        )

        # ``response_format="text"`` returns a plain string, not a JSON object
        text: str = (
            result if isinstance(result, str) else getattr(result, "text", str(result))
        )
        logger.info("Transcription complete: %d chars", len(text))
        return text.strip()

    # ── Text-to-Speech ────────────────────────────────────────────────────────

    async def stream_tts(
        self,
        *,
        text: str,
        voice: str = "",
        model: str = "",
        response_format: str = "",
        instructions: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        """Stream TTS audio chunks via OpenAI's speech synthesis API."""
        effective_model = model or self._default_tts_model
        effective_voice = voice or self._default_voice
        effective_fmt = response_format or self._default_tts_format

        logger.info(
            "TTS request: model=%s voice=%s format=%s chars=%d",
            effective_model,
            effective_voice,
            effective_fmt,
            len(text),
        )

        kwargs: dict = {}
        # ``instructions`` is only supported by gpt-4o-mini-tts
        if instructions and effective_model == "gpt-4o-mini-tts":
            kwargs["instructions"] = instructions

        # ``with_streaming_response`` keeps the TCP connection open and lets us
        # iterate over raw binary chunks without buffering the full audio in
        # memory.
        async with self._client.audio.speech.with_streaming_response.create(
            model=effective_model,
            voice=effective_voice,
            input=text,
            response_format=effective_fmt,  # type: ignore[arg-type]
            **kwargs,
        ) as resp:
            async for chunk in resp.iter_bytes(chunk_size=4096):
                yield chunk

    # ── Speech-to-Speech (S2S) ────────────────────────────────────────────────

    @property
    def supports_s2s(self) -> bool:
        return True

    async def create_s2s_session(
        self,
        *,
        model: str = "",
        voice: str = "",
        instructions: Optional[str] = None,
    ) -> dict:
        """Mint a short-lived ephemeral token for an OpenAI Realtime S2S session.

        The token is passed to the browser so it can authenticate the
        ``/audio/realtime`` WebSocket proxy without ever receiving the main
        API key.  The Realtime sessions endpoint is not yet part of the typed
        SDK surface, so we use raw ``httpx``.
        """
        import httpx

        effective_model = model or self._realtime_model
        effective_voice = voice or self._default_voice

        logger.info(
            "Creating S2S session: model=%s voice=%s", effective_model, effective_voice
        )

        body: dict = {
            "model": effective_model,
            "voice": effective_voice,
            "modalities": ["audio", "text"],
            "turn_detection": {"type": "server_vad"},
        }
        if instructions:
            body["instructions"] = instructions

        async with httpx.AsyncClient() as http:
            resp = await http.post(
                "https://api.openai.com/v1/realtime/sessions",
                json=body,
                headers={
                    "Authorization": f"Bearer {self._client.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()

        logger.info("S2S session created, expires_at=%s", data.get("expires_at"))
        return data

    def s2s_ws_url(self, model: str) -> str:
        """Return the OpenAI Realtime WebSocket URL for S2S sessions."""
        return f"{self._REALTIME_UPSTREAM}?model={model}"


# ── Internal helpers ──────────────────────────────────────────────────────────


def _mime_for(filename: str) -> str:
    """Return a plausible MIME type based on the file extension."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return {
        "mp3": "audio/mpeg",
        "mp4": "audio/mp4",
        "mpeg": "audio/mpeg",
        "mpga": "audio/mpeg",
        "m4a": "audio/mp4",
        "ogg": "audio/ogg",
        "wav": "audio/wav",
        "webm": "audio/webm",
        "flac": "audio/flac",
    }.get(ext, "audio/octet-stream")
