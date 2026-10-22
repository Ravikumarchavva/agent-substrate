"""Text-to-speech providers."""

from loamz.engine.agents.providers.tts.kokoro_client import KokoroTTSClient, get_kokoro_client

__all__ = ["KokoroTTSClient", "get_kokoro_client"]
