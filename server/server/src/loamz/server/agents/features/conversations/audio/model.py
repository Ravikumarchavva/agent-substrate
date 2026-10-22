from __future__ import annotations

from pydantic import BaseModel


class TranscribeResponse(BaseModel):
    text: str


class TTSRequest(BaseModel):
    text: str
    model: str | None = None
    voice: str | None = None
    response_format: str | None = None
    """mp3 | opus | aac | flac | wav | pcm"""
    instructions: str | None = None
    """A style hint (gpt-4o-mini-tts only)."""


class RealtimeTokenResponse(BaseModel):
    client_secret: str
    expires_at: int
    session_id: str
