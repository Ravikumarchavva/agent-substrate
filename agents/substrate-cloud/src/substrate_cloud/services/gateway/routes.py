"""Gateway BFF: the Agent API (``/api/v1``) of microservices mode.

The gateway is the only public service. It serves the same routes and documents as the monolith (``contracts/agents/v1``) and
composes the internal services behind them, forwarding the caller's own token so every service applies the caller's tenant and
user. It holds no business logic and trusts no identity in a request body.

  /threads, /feedbacks, /threads/{id}/messages|feedback     Conversation service
  POST /chat                                                Policy (may submit?) -> Job controller (start) -> Live stream (events)
  POST /chat/{thread_id}/cancel                             Job controller
  POST /chat/respond/{request_id}, GET /hitl/status/{id}    Human gate
  GET  /stream/{thread_id}                                  Live stream (reconnect)
  /files/...                                                the caller's file store (``files.py``)
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any, List, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from substrate_cloud.shared.contracts.conversation import (
    ChatMessage,
    FeedbackCreate,
    FeedbackOut,
    ThreadCreate,
    ThreadOut,
    ThreadUpdate,
)
from substrate_cloud.shared.contracts.human_gate import HITLResponse
from substrate_cloud.shared.problem import Problem
from substrate_cloud.shared.service_client import ServiceClient

logger = logging.getLogger(__name__)

router = APIRouter()

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def token_of(request: Request) -> str:
    """The caller's raw bearer token, to forward. (It is verified before any handler runs.)"""
    header = request.headers.get("authorization", "")
    return header[7:] if header.lower().startswith("bearer ") else ""


def _client(request: Request, name: str) -> ServiceClient:
    return getattr(request.app.state, f"{name}_client")


# ── Threads ──────────────────────────────────────────────────────────────────


@router.post("/threads", response_model=ThreadOut, status_code=201, tags=["threads"])
async def create_thread(body: ThreadCreate, request: Request) -> Any:
    return await _client(request, "conversation").json(
        "POST", "/threads", token_of(request), json=body.model_dump()
    )


@router.get("/threads", response_model=List[ThreadOut], tags=["threads"])
async def list_threads(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    archived: bool = False,
) -> Any:
    return await _client(request, "conversation").json(
        "GET",
        "/threads",
        token_of(request),
        params={"limit": limit, "offset": offset, "archived": archived},
    )


@router.get("/threads/{thread_id}", response_model=ThreadOut, tags=["threads"])
async def get_thread(thread_id: uuid.UUID, request: Request) -> Any:
    return await _client(request, "conversation").json(
        "GET", f"/threads/{thread_id}", token_of(request)
    )


@router.patch("/threads/{thread_id}", response_model=ThreadOut, tags=["threads"])
async def update_thread(
    thread_id: uuid.UUID, body: ThreadUpdate, request: Request
) -> Any:
    return await _client(request, "conversation").json(
        "PATCH",
        f"/threads/{thread_id}",
        token_of(request),
        json=body.model_dump(exclude_unset=True),
    )


@router.delete("/threads/{thread_id}", status_code=204, tags=["threads"])
async def delete_thread(thread_id: uuid.UUID, request: Request) -> Response:
    await _client(request, "conversation").request(
        "DELETE", f"/threads/{thread_id}", token_of(request)
    )
    return Response(status_code=204)


@router.get("/threads/{thread_id}/messages", tags=["threads"])
async def get_thread_messages(thread_id: uuid.UUID, request: Request) -> Any:
    return await _client(request, "conversation").json(
        "GET", f"/threads/{thread_id}/messages", token_of(request)
    )


@router.get(
    "/threads/{thread_id}/feedback", response_model=List[FeedbackOut], tags=["feedback"]
)
async def thread_feedback(thread_id: uuid.UUID, request: Request) -> Any:
    return await _client(request, "conversation").json(
        "GET", f"/threads/{thread_id}/feedback", token_of(request)
    )


@router.post(
    "/feedbacks", response_model=FeedbackOut | None, status_code=201, tags=["feedback"]
)
async def submit_feedback(body: FeedbackCreate, request: Request) -> Any:
    return await _client(request, "conversation").json(
        "POST", "/feedbacks", token_of(request), json=body.model_dump(mode="json")
    )


# ── Chat ─────────────────────────────────────────────────────────────────────


class ChatBody(BaseModel):
    thread_id: uuid.UUID
    messages: List[ChatMessage]
    system_instructions: Optional[str] = None
    file_ids: Optional[List[uuid.UUID]] = None


async def relay(upstream: Any) -> AsyncIterator[bytes]:
    """The upstream event stream, byte for byte. The run keeps going if the client leaves; only this connection closes."""
    try:
        async for chunk in upstream.aiter_bytes():
            yield chunk
    except (
        Exception
    ) as exc:  # the stream is open: report in-band, then end it like every stream
        logger.exception("Event stream interrupted")
        yield f'data: {{"type": "error", "message": "{type(exc).__name__}"}}\n\n'.encode()
        yield b"data: [DONE]\n\n"
    finally:
        await upstream.aclose()


def _sse(upstream: Any) -> StreamingResponse:
    return StreamingResponse(
        relay(upstream), media_type="text/event-stream", headers=SSE_HEADERS
    )


@router.post("/chat", tags=["chat"])
async def chat(body: ChatBody, request: Request) -> StreamingResponse:
    """Start a run and stream it as server-sent events (the monolith's ``POST /chat``).

    The caller must be allowed to submit (policy; if the policy service is unreachable the request is refused, not allowed), must own
    the thread, and a thread has one run at a time (409).
    """
    if not body.messages:
        raise Problem(422, "invalid-request", "messages[] must not be empty")
    token = token_of(request)

    decision = await _client(request, "policy").json(
        "POST", "/policy/check", token, json={"action": "submit_conversation"}
    )
    if not decision.get("allowed", False):
        raise Problem(403, "forbidden", "Not authorized to submit a conversation")

    run = await _client(request, "job_controller").json(
        "POST",
        "/jobs/runs",
        token,
        json={
            "thread_id": str(body.thread_id),
            "user_content": body.messages[-1].content,
            "system_instructions": body.system_instructions,
            "file_ids": [str(f) for f in body.file_ids or []],
        },
        timeout=60.0,
    )
    upstream = await _client(request, "live_stream").open_stream(
        f"/stream/runs/{run['runtime_run_id']}", token
    )
    return _sse(upstream)


@router.post("/chat/respond/{request_id}", tags=["chat"])
async def respond_to_hitl(request_id: str, body: HITLResponse, request: Request) -> Any:
    return await _client(request, "human_gate").json(
        "POST",
        f"/hitl/respond/{request_id}",
        token_of(request),
        json=body.model_dump(exclude_none=True),
    )


@router.post("/chat/{thread_id}/cancel", tags=["chat"])
async def cancel_chat(thread_id: uuid.UUID, request: Request) -> dict:
    """``{"status": "cancelled"}`` when a run was going, ``{"status": "not_found"}`` when none was."""
    result = await _client(request, "job_controller").json(
        "POST", f"/jobs/threads/{thread_id}/cancel", token_of(request)
    )
    return {"status": result["status"], "thread_id": str(thread_id)}


@router.get("/hitl/status/{thread_id}", tags=["chat"])
async def hitl_status(thread_id: uuid.UUID, request: Request) -> Any:
    return await _client(request, "human_gate").json(
        "GET", f"/hitl/status/{thread_id}", token_of(request)
    )


@router.get("/stream/{thread_id}", tags=["chat"])
async def stream_thread(thread_id: uuid.UUID, request: Request) -> StreamingResponse:
    """Reconnect to a thread's active run (``[DONE]`` at once when none is)."""
    upstream = await _client(request, "live_stream").open_stream(
        f"/stream/{thread_id}", token_of(request)
    )
    return _sse(upstream)
