"""Audio service shim — re-exports shared audio type aliases.

Routes and other callers should use ``request.app.state.model_client``
(an ``LLMClient`` instance) directly rather than importing service functions.
"""

from __future__ import annotations

# Re-export shared type aliases so existing ``from audio_service import …``
# statements keep working without change.
from typing import Literal

STT_MODEL = Literal[
    "whisper-1",
    "gpt-4o-transcribe",
    "gpt-4o-mini-transcribe",
]

# Keep these in sync with the audio formats supported by the browser / provider
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

__all__ = ["STT_MODEL", "TTS_FORMAT", "TTS_VOICE"]
