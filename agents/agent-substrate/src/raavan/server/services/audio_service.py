"""Audio service shim — re-exports shared audio type aliases.

The implementation has moved into the unified model client layer::

    raavan.core.llm.base_client   ← BaseModelClient ABC (incl. audio methods)
    raavan.integrations.llm.openai ← OpenAIClient (handles text + audio + vision)

Routes and other callers should use ``request.app.state.model_client``
(a ``BaseModelClient`` instance) directly rather than importing service
functions.  The re-exports below are kept for backward compatibility only.
"""

from __future__ import annotations

# Re-export shared type aliases so existing ``from audio_service import …``
# statements keep working without change.
from raavan.core.llm.base_client import (  # noqa: F401
    STT_MODEL,
    TTS_FORMAT,
    TTS_VOICE,
)

__all__ = ["STT_MODEL", "TTS_FORMAT", "TTS_VOICE"]
