"""Kokoro-82M local text-to-speech adapter."""

from __future__ import annotations

import asyncio
import io
from collections.abc import AsyncGenerator
from typing import Any


class KokoroTTSClient:
    """Lazy local TTS client backed by the Kokoro-82M model.

    The pipeline and model weights are loaded on the first request, not during
    application startup. Kokoro currently produces complete WAV samples, so
    ``stream_tts`` yields one audio chunk after synthesis.
    """

    provider = "local"

    def __init__(self, model: str = "kokoro-82m") -> None:
        self.model = model
        self._pipeline: Any | None = None
        self._load_lock = asyncio.Lock()

    async def _get_pipeline(self) -> Any:
        if self._pipeline is None:
            async with self._load_lock:
                if self._pipeline is None:
                    self._pipeline = await asyncio.to_thread(self._load_pipeline)
        return self._pipeline

    def _load_pipeline(self) -> Any:
        from kokoro import KPipeline

        return KPipeline(lang_code="a")

    async def stream_tts(
        self,
        *,
        text: str,
        voice: str = "af_heart",
        model: str = "",
        response_format: str = "wav",
        instructions: str | None = None,
    ) -> AsyncGenerator[bytes, None]:
        if response_format.lower() != "wav":
            raise ValueError("Kokoro local TTS currently supports WAV output only")
        if not text.strip():
            raise ValueError("TTS text cannot be empty")

        pipeline = await self._get_pipeline()
        audio = await asyncio.to_thread(self._synthesize, pipeline, text, voice)
        yield audio

    @staticmethod
    def _synthesize(pipeline: Any, text: str, voice: str) -> bytes:
        import soundfile as sf

        chunks = [audio for _, _, audio in pipeline(text, voice=voice, speed=1.0)]
        if not chunks:
            raise RuntimeError("Kokoro returned no audio")

        import numpy as np

        samples = np.concatenate(chunks)
        output = io.BytesIO()
        sf.write(output, samples, 24_000, format="WAV", subtype="PCM_16")
        return output.getvalue()


__all__ = ["KokoroTTSClient"]


_DEFAULT_CLIENT = KokoroTTSClient()


def get_kokoro_client() -> KokoroTTSClient:
    """Return the process-wide lazy client for the default local model."""
    return _DEFAULT_CLIENT


__all__.append("get_kokoro_client")
