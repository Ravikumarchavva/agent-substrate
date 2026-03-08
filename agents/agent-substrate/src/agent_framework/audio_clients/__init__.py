"""Provider-agnostic audio client layer.

Mirrors the ``model_clients`` package pattern — the framework only depends on
``BaseAudioClient``; provider-specific code lives in sub-packages.

Usage::

    from agent_framework.audio_clients import BaseAudioClient
    from agent_framework.audio_clients.openai import OpenAIAudioClient
"""

from agent_framework.audio_clients.base_audio_client import BaseAudioClient

__all__ = ["BaseAudioClient"]
