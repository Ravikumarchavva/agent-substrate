"""Provider-agnostic base class for all audio operations.

Mirrors ``BaseModelClient`` — subclass and override the abstract methods to
support a new provider (OpenAI, Google, Azure, ElevenLabs, …).

Capabilities
------------
1. **Transcription (STT)** — ``transcribe()``
   Convert audio bytes to text (Whisper-style).

2. **Text-to-Speech (TTS)** — ``stream_tts()``
   Stream synthesised audio for a given text string.

3. **Speech-to-Speech (S2S)** — ``create_s2s_session()`` + ``s2s_ws_url()``
   Mint an ephemeral token and expose a WebSocket URL so the browser can
   hold a live, bidirectional voice conversation with the model.  Not all
   providers support this; those that do override the two S2S methods.

The framework only ever imports ``BaseAudioClient``; no vendor SDK is
imported here so the core remains dependency-free.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import AsyncGenerator, Literal, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared type literals (provider-neutral names; sub-classes may not support
# every value — they should raise ValueError for unsupported options).
# ---------------------------------------------------------------------------

STT_MODEL = Literal[
    "whisper-1",
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
]

TTS_VOICE = Literal[
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
    "marin",
    "cedar",
]

TTS_FORMAT = Literal["mp3", "opus", "aac", "flac", "wav", "pcm"]


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------


class BaseAudioClient(ABC):
    """Provider-agnostic interface for STT, TTS, and Speech-to-Speech.

    All audio operations in routes and services go through this interface so
    the rest of the framework never imports a vendor SDK class directly.

    To add a new provider
    ~~~~~~~~~~~~~~~~~~~~~
    1. Create ``audio_clients/<provider>/<provider>_audio_client.py``.
    2. Subclass ``BaseAudioClient`` and implement ``transcribe`` and
       ``stream_tts`` (required).
    3. Optionally override ``create_s2s_session`` and ``s2s_ws_url`` if the
       provider supports live speech-to-speech sessions.
    4. Instantiate in ``server/app.py`` and assign to
       ``app.state.audio_client``.
    """

    # ── 1. Transcription (STT) ────────────────────────────────────────────────

    @abstractmethod
    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        model: str = "whisper-1",
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> str:
        """Transcribe ``audio_bytes`` to text.

        Args:
            audio_bytes: Raw audio bytes (mp3, wav, webm, m4a, …; max 25 MB).
            filename:    Original filename — used to derive the MIME type.
            model:       Provider-specific STT model identifier.
            language:    Optional ISO 639-1 language hint (e.g. ``"en"``).
            prompt:      Optional context hint to steer the model.

        Returns:
            Transcribed text string (stripped of leading/trailing whitespace).
        """

    # ── 2. Text-to-Speech (TTS) ───────────────────────────────────────────────

    @abstractmethod
    def stream_tts(
        self,
        *,
        text: str,
        voice: str = "coral",
        model: str = "tts-1",
        response_format: str = "mp3",
        instructions: Optional[str] = None,
    ) -> AsyncGenerator[bytes, None]:
        """Yield raw audio bytes for ``text`` using TTS synthesis.

        The implementation should use a streaming path so the first byte
        arrives as fast as possible — the HTTP layer can start writing to
        the response immediately.

        Args:
            text:            The text to synthesise.
            voice:           Voice identifier (provider-specific).
            model:           TTS model identifier (provider-specific).
            response_format: Audio container format (mp3, wav, opus, …).
            instructions:    Optional style/tone instructions (if supported).
        """

    # ── 3. Speech-to-Speech (S2S / Realtime) ─────────────────────────────────
    #
    # Live bidirectional voice conversation: the browser captures microphone
    # audio, streams it to the model, and plays back synthesised audio in
    # real-time — all over a single WebSocket.
    #
    # Flow:
    #   Browser  ──audio──►  WS /audio/realtime  ──►  provider upstream WS
    #   Browser  ◄──audio──  WS /audio/realtime  ◄──  provider upstream WS
    #
    # Step 1: call ``create_s2s_session()`` server-side to mint an ephemeral
    #         token that the browser sends as its first WS auth message.
    # Step 2: the WS proxy calls ``s2s_ws_url(model)`` to know where to
    #         forward the browser connection.
    #
    # Not all providers implement S2S.  The default methods raise
    # ``NotImplementedError``; override both to enable S2S for a new provider.

    @property
    def supports_s2s(self) -> bool:
        """Return ``True`` if this client supports speech-to-speech sessions.

        Routes use this flag to return HTTP 501 early rather than letting the
        ``NotImplementedError`` propagate as a 500.
        """
        return False

    async def create_s2s_session(
        self,
        *,
        model: str = "",
        voice: str = "coral",
        instructions: Optional[str] = None,
    ) -> dict:
        """Mint a short-lived ephemeral token for a speech-to-speech session.

        The browser sends this token as the first message on the
        ``/audio/realtime`` WebSocket so it can authenticate without ever
        receiving the server's main API key.

        Returns a dict containing at least:
            ``client_secret`` – the short-lived ephemeral token.
            ``expires_at``    – Unix timestamp when the token expires.
            ``id``            – Session identifier.

        Raises:
            NotImplementedError: if this provider does not support S2S.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support speech-to-speech sessions. "
            "Override create_s2s_session() and s2s_ws_url() to add support."
        )

    def s2s_ws_url(self, model: str) -> str:
        """Return the upstream WebSocket URL for a speech-to-speech session.

        The WS proxy route calls this to know where to relay the browser
        connection using the ephemeral token from ``create_s2s_session()``.

        Raises:
            NotImplementedError: if this provider does not support S2S.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not provide a speech-to-speech WebSocket URL. "
            "Override s2s_ws_url() to add support."
        )
