"""Audio service shim — re-exports from the provider-agnostic audio_clients layer.

The implementation has moved to::

    agent_framework.audio_clients.base_audio_client   ← BaseAudioClient ABC
    agent_framework.audio_clients.openai              ← OpenAIAudioClient

Routes and other callers should use ``request.app.state.audio_client``
(a ``BaseAudioClient`` instance) directly rather than importing service
functions.  The re-exports below are kept for backward compatibility only.
"""

from __future__ import annotations

# Re-export shared type aliases so existing ``from audio_service import …``
# statements keep working without change.
from agent_framework.audio_clients.base_audio_client import (  # noqa: F401
    STT_MODEL,
    TTS_FORMAT,
    TTS_VOICE,
)

__all__ = ["STT_MODEL", "TTS_FORMAT", "TTS_VOICE"]
