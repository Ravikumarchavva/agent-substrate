from __future__ import annotations

from abc import ABC, abstractmethod
from typing import (
    TYPE_CHECKING,
    Any,
    AsyncGenerator,
    AsyncIterator,
    Literal,
    Optional,
    Type,
)

from raavan.core.messages.client_messages import (
    BaseClientMessage,
    AssistantMessage,
)

if TYPE_CHECKING:
    from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Audio type literals (provider-neutral; implementors raise ValueError for
# unsupported values).
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


class BaseModelClient(ABC):
    """Base class for all model clients (OpenAI, Anthropic, etc.).

    Core text/vision capabilities (``generate``, ``generate_stream``,
    ``count_tokens``) are **abstract** — every provider must implement them.

    Audio capabilities (``transcribe``, ``stream_tts``, ``create_s2s_session``,
    ``s2s_ws_url``) are **optional** — they default to raising
    ``NotImplementedError``.  Providers that support audio override them.
    Check ``client.supports_audio`` / ``client.supports_s2s`` before calling.

    AssistantMessage content is ``Optional[list[MediaType]]`` where
    ``MediaType = Union[str, Image.Image, AudioContent, VideoContent]`` —
    the same message type carries text, images, and audio output.
    """

    def __init__(
        self,
        model: str,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs,
    ):
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.kwargs = kwargs

    # ── Text / Vision (required) ──────────────────────────────────────────────

    @abstractmethod
    async def generate(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict]] = None,
        *,
        tool_choice: Optional[str | dict[str, Any]] = None,
        response_format: Optional["Type[BaseModel]"] = None,
        **kwargs: Any,
    ) -> Any:
        """Generate a response from the model.

        - When ``response_format`` is ``None``: returns ``AssistantMessage``.
        - When ``response_format`` is a Pydantic schema: returns
          ``StructuredOutputResult``.
        """
        raise NotImplementedError

    @abstractmethod
    async def generate_stream(
        self,
        messages: list[BaseClientMessage],
        tools: Optional[list[dict]] = None,
        *,
        response_format: Optional["Type[BaseModel]"] = None,
        **kwargs,
    ) -> AsyncIterator[AssistantMessage]:
        """Generate a streaming response from the model."""
        if False:
            # Marks this abstract method as an async-generator contract.
            yield AssistantMessage(role="assistant", content=None)

    @abstractmethod
    async def count_tokens(self, messages: list[BaseClientMessage]) -> int:
        """Count tokens in messages."""
        pass

    # ── Audio capabilities (optional) ────────────────────────────────────────

    @property
    def supports_audio(self) -> bool:
        """Return ``True`` if this client supports STT / TTS."""
        return False

    @property
    def supports_s2s(self) -> bool:
        """Return ``True`` if this client supports live speech-to-speech sessions."""
        return False

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        model: str = "whisper-1",
        language: Optional[str] = None,
        prompt: Optional[str] = None,
    ) -> str:
        """Transcribe ``audio_bytes`` to text (STT).

        Override in providers that support audio.  Routes should check
        ``client.supports_audio`` first and return HTTP 501 if ``False``.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support audio transcription"
        )

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

        Override in providers that support audio.  Routes should check
        ``client.supports_audio`` first and return HTTP 501 if ``False``.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support text-to-speech"
        )

    async def create_s2s_session(
        self,
        *,
        model: str = "",
        voice: str = "",
        instructions: Optional[str] = None,
    ) -> dict:
        """Mint an ephemeral token for a live speech-to-speech session.

        Override in providers that support S2S.  Routes should check
        ``client.supports_s2s`` first and return HTTP 501 if ``False``.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support speech-to-speech sessions"
        )

    def s2s_ws_url(self, model: str) -> str:
        """Return the provider WebSocket URL for S2S sessions."""
        raise NotImplementedError(
            f"{type(self).__name__} does not support speech-to-speech sessions"
        )

    # ── Vision / Image generation (optional) ────────────────────────────────

    @property
    def supports_image_generation(self) -> bool:
        """Return ``True`` if this client can generate images."""
        return False

    async def generate_image(
        self,
        prompt: str,
        *,
        n: int = 1,
        size: str = "1024x1024",
        quality: str = "standard",
        style: Optional[str] = None,
        model: str = "",
        **kwargs: Any,
    ) -> list[str]:
        """Generate images from a text prompt.

        Returns a list of image URLs or base-64 data URL strings.
        Override in providers that support image generation.  Routes should
        check ``client.supports_image_generation`` first.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support image generation"
        )
