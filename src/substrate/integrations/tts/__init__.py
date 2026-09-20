"""Local text-to-speech integrations."""

from substrate.integrations.tts.kokoro_client import KokoroTTSClient, get_kokoro_client

__all__ = ["KokoroTTSClient", "get_kokoro_client"]
