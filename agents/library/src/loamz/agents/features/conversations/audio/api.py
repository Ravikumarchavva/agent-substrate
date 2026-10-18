from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse

from loamz.agents.bootstrap.http import CallerDep, use
from loamz.agents.features.conversations.audio.model import (
    RealtimeTokenResponse,
    TranscribeResponse,
    TTSRequest,
)
from loamz.agents.features.conversations.audio.service import AudioService, audio

logger = logging.getLogger(__name__)

Service = Annotated[AudioService, Depends(use(audio))]

router = APIRouter(prefix="/api/v1/audio", tags=["audio"])

REALTIME_IDLE_TIMEOUT = 120.0
"""If the browser sends nothing for this long, the realtime proxy closes."""


@router.post("/transcribe", response_model=TranscribeResponse)
async def transcribe_audio(
    caller: CallerDep,
    service: Service,
    file: Annotated[UploadFile, File(description="Audio file (mp3/wav/webm/m4a, max 25 MB)")],
    model: Annotated[str, Form()] = "",
    language: Annotated[str | None, Form()] = None,
    prompt: Annotated[str | None, Form()] = None,
) -> TranscribeResponse:
    raw = await file.read()
    text = await service.transcribe(
        raw, file.filename or "audio.webm", model=model, language=language, prompt=prompt
    )
    return TranscribeResponse(text=text)


@router.post("/tts")
async def text_to_speech(
    body: TTSRequest, caller: CallerDep, service: Service
) -> StreamingResponse:
    """Text to speech, streamed back as audio."""
    speech = await service.speak(body)
    return StreamingResponse(
        speech.chunks,
        media_type=speech.content_type,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/realtime-token", response_model=RealtimeTokenResponse)
async def get_realtime_token(
    caller: CallerDep, service: Service, model: str = "", voice: str = ""
) -> RealtimeTokenResponse:
    return await service.realtime_token(model, voice)


@router.websocket("/realtime")
async def realtime_proxy(websocket: WebSocket, service: Service) -> None:
    """Browser <-> this server <-> the provider's realtime websocket.

    The browser's first message is `{"type": "auth", "client_secret": <ephemeral token from /realtime-token>}`; the
    secret is the credential (a browser cannot set headers on a websocket), and we open the upstream connection with
    it as the bearer. Idle sessions close after `REALTIME_IDLE_TIMEOUT`.
    """
    import websockets
    from websockets.exceptions import ConnectionClosed

    await websocket.accept()
    try:
        auth = json.loads(await asyncio.wait_for(websocket.receive_text(), timeout=10.0))
    except TimeoutError, json.JSONDecodeError, RuntimeError:
        await websocket.close(code=4001, reason="Expected auth message")
        return
    if auth.get("type") != "auth" or not auth.get("client_secret"):
        await websocket.close(code=4002, reason="Missing client_secret in auth message")
        return
    url = service.realtime_url(auth.get("model"))
    if url is None:
        await websocket.close(code=4005, reason="Realtime is not available for this model")
        return
    try:
        async with websockets.connect(
            url,
            additional_headers={
                "Authorization": f"Bearer {auth['client_secret']}",
                "OpenAI-Beta": "realtime=v1",
            },
        ) as upstream:

            async def up() -> None:
                while True:
                    try:
                        message = await asyncio.wait_for(
                            websocket.receive_text(), timeout=REALTIME_IDLE_TIMEOUT
                        )
                        await upstream.send(message)
                    except TimeoutError:
                        await upstream.close()
                        await websocket.close(code=4003, reason="Idle timeout")
                        return
                    except WebSocketDisconnect, ConnectionClosed:
                        return

            async def down() -> None:
                try:
                    async for message in upstream:
                        try:
                            await websocket.send_text(
                                message if isinstance(message, str) else message.decode()
                            )
                        except WebSocketDisconnect, RuntimeError:
                            return
                except ConnectionClosed:
                    pass

            tasks = [asyncio.create_task(up()), asyncio.create_task(down())]
            _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
    except Exception:
        logger.exception("Realtime proxy error")
        try:
            await websocket.close(code=4004, reason="Upstream connection failed")
        except RuntimeError:
            pass
