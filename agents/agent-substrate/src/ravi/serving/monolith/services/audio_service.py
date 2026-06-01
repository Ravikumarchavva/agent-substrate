"""Audio service shim — re-exports shared audio type aliases.

The implementation has moved into the unified model client layer::

    ravi.kernel.llm.base_client   ← BaseModelClient ABC (incl. audio methods)
    ravi.adapters.llm.openai ← OpenAIClient (handles text + audio + vision)

Routes and other callers should use ``request.app.state.model_client``
(a ``BaseModelClient`` instance) directly rather than importing service
functions.  The re-exports below are kept for backward compatibility only.
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
