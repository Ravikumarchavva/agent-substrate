"""Audio routes — transcription (STT), text-to-speech (TTS), Realtime WS proxy.

Endpoints
---------
POST  /audio/transcribe          STT → {"text": "..."}
POST  /audio/tts                 TTS streaming → audio/mpeg
GET   /audio/realtime-token      Mint ephemeral Realtime session token
WS    /audio/realtime            Backend proxy to provider Realtime WS

All audio operations are delegated to ``request.app.state.audio_client``
(a ``BaseAudioClient`` instance) so routes contain zero provider-specific code.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from agent_framework.audio_clients import BaseAudioClient
from agent_framework.server.schemas import (
    RealtimeTokenResponse,
    TranscribeResponse,
    TTSRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/audio", tags=["audio"])

# Maximum upload size for audio files (25 MB — OpenAI hard limit)
_MAX_AUDIO_BYTES = 25 * 1024 * 1024

# Inactivity timeout for the Realtime proxy (seconds).
# If the browser stops sending for this long the WS is closed automatically.
_REALTIME_IDLE_TIMEOUT = 120.0


# ── POST /audio/transcribe ────────────────────────────────────────────────────

@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    request: Request,
    file: Annotated[UploadFile, File(description="Audio file (mp3/wav/webm/m4a, max 25 MB)")],
    model: Annotated[str, Form()] = "whisper-1",
    language: Annotated[str | None, Form()] = None,
    prompt: Annotated[str | None, Form()] = None,
):
    """Transcribe an uploaded audio file to text.

    Delegates to ``app.state.audio_client`` (a ``BaseAudioClient``) so the
    route is fully provider-agnostic.
    """
    raw = await file.read()
    if len(raw) > _MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio file exceeds 25 MB limit")

    if not raw:
        raise HTTPException(status_code=400, detail="Empty audio file")

    audio_client: BaseAudioClient = request.app.state.audio_client

    try:
        text = await audio_client.transcribe(
            audio_bytes=raw,
            filename=file.filename or "audio.webm",
            model=model,
            language=language or None,
            prompt=prompt or None,
        )
    except Exception as exc:
        logger.exception("Transcription failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return TranscribeResponse(text=text)


# ── POST /audio/tts ───────────────────────────────────────────────────────────

_TTS_CONTENT_TYPE = {
    "mp3": "audio/mpeg",
    "opus": "audio/ogg; codecs=opus",
    "aac": "audio/aac",
    "flac": "audio/flac",
    "wav": "audio/wav",
    "pcm": "audio/pcm",
}


@router.post("/tts")
async def text_to_speech(request: Request, body: TTSRequest):
    """Convert text to speech and stream the audio back.

    The response is a raw audio stream with the appropriate Content-Type.
    The client should treat it as a blob and play it via the Web Audio API
    or an ``<audio>`` element.

    Delegates to ``app.state.audio_client`` — provider-agnostic.
    """
    audio_client: BaseAudioClient = request.app.state.audio_client
    fmt = body.response_format or "mp3"
    content_type = _TTS_CONTENT_TYPE.get(fmt, "audio/mpeg")

    try:
        audio_iter = audio_client.stream_tts(
            text=body.text,
            voice=body.voice or "coral",
            model=body.model or "gpt-4o-mini-tts",
            response_format=fmt,
            instructions=body.instructions,
        )
    except Exception as exc:
        logger.exception("TTS setup failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return StreamingResponse(
        audio_iter,
        media_type=content_type,
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


# ── GET /audio/realtime-token ─────────────────────────────────────────────────

@router.get("/realtime-token", response_model=RealtimeTokenResponse)
async def get_realtime_token(request: Request):
    """Mint a short-lived ephemeral Realtime session token.

    The browser uses this token to authenticate the ``/audio/realtime``
    WebSocket proxy without ever receiving the server's main API key.

    Delegates to ``app.state.audio_client`` — provider-agnostic.
    """
    audio_client: BaseAudioClient = request.app.state.audio_client
    system_instructions: str = getattr(request.app.state, "system_instructions", "")

    if not audio_client.supports_s2s:
        raise HTTPException(status_code=501, detail="Speech-to-speech not supported by the configured audio provider")

    try:
        session = await audio_client.create_s2s_session(
            instructions=system_instructions or None,
        )
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Failed to create S2S session")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    client_secret = (
        session.get("client_secret", {}).get("value", "")
        if isinstance(session.get("client_secret"), dict)
        else session.get("client_secret", "")
    )

    return RealtimeTokenResponse(
        client_secret=client_secret,
        expires_at=session.get("expires_at", 0),
        session_id=session.get("id", ""),
    )


# ── WS /audio/realtime ────────────────────────────────────────────────────────

@router.websocket("/realtime")
async def realtime_proxy(websocket: WebSocket):
    """Backend WebSocket proxy to OpenAI Realtime API.

    Flow:
      Browser ←→ WS /audio/realtime ←→ wss://api.openai.com/v1/realtime

    The browser sends us an ephemeral ``client_secret`` as the first JSON
    message:
        {"type": "auth", "client_secret": "<token>"}

    We then open the upstream WS with the correct ``Authorization`` header
    (something the browser cannot do with the native WebSocket API), and
    bidirectionally relay all subsequent messages.

    Idle timeout: if no message is received from the browser for
    ``_REALTIME_IDLE_TIMEOUT`` seconds the proxy closes both connections.
    This prevents forgotten sessions from accumulating.
    """
    import websockets
    from websockets.exceptions import ConnectionClosed

    await websocket.accept()

    # ── Step 1: read auth message from browser ────────────────────────────
    try:
        raw_auth = await asyncio.wait_for(websocket.receive_text(), timeout=10.0)
        auth_msg = json.loads(raw_auth)
    except (asyncio.TimeoutError, json.JSONDecodeError, RuntimeError):
        await websocket.close(code=4001, reason="Expected auth message")
        return

    if auth_msg.get("type") != "auth" or not auth_msg.get("client_secret"):
        await websocket.close(code=4002, reason="Missing client_secret in auth message")
        return

    client_secret: str = auth_msg["client_secret"]
    model: str = auth_msg.get("model", "gpt-4o-realtime-preview-2024-12-17")

    # Ask the audio client for the upstream URL — keeps provider details out
    # of this route.
    audio_client: BaseAudioClient = websocket.app.state.audio_client
    try:
        upstream_url = audio_client.s2s_ws_url(model)
    except NotImplementedError:
        await websocket.close(code=4005, reason="Realtime not supported by this audio provider")
        return

    logger.info("Realtime proxy: connecting upstream model=%s", model)

    # ── Step 2: open upstream connection with the secret ──────────────────
    try:
        async with websockets.connect(
            upstream_url,
            additional_headers={
                "Authorization": f"Bearer {client_secret}",
                "OpenAI-Beta": "realtime=v1",
            },
        ) as upstream:
            logger.info("Realtime proxy: upstream connected")

            async def browser_to_upstream():
                """Relay messages from browser → OpenAI, reset idle timer."""
                while True:
                    try:
                        msg = await asyncio.wait_for(
                            websocket.receive_text(),
                            timeout=_REALTIME_IDLE_TIMEOUT,
                        )
                        await upstream.send(msg)
                    except asyncio.TimeoutError:
                        logger.warning("Realtime proxy: browser idle timeout, closing")
                        await upstream.close()
                        await websocket.close(code=4003, reason="Idle timeout")
                        return
                    except (WebSocketDisconnect, ConnectionClosed):
                        return

            async def upstream_to_browser():
                """Relay messages from OpenAI → browser."""
                try:
                    async for msg in upstream:
                        try:
                            await websocket.send_text(
                                msg if isinstance(msg, str) else msg.decode()
                            )
                        except (WebSocketDisconnect, RuntimeError):
                            return
                except ConnectionClosed:
                    pass

            # Run both directions concurrently; either exiting cancels the other
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(browser_to_upstream()),
                    asyncio.create_task(upstream_to_browser()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except Exception as exc:
        logger.exception("Realtime proxy error: %s", exc)
        try:
            await websocket.close(code=4004, reason="Upstream connection failed")
        except RuntimeError:
            pass

    logger.info("Realtime proxy: session ended")
