from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from loamz.agents.bootstrap.http import CallerDep, use
from loamz.agents.features.conversations.chat.model import (
    ApprovalOut,
    CancelResponse,
    ChatRequest,
    HitlResponse,
    HitlStatus,
)
from loamz.agents.features.conversations.chat.service import ChatService, chat

Service = Annotated[ChatService, Depends(use(chat))]

router = APIRouter(tags=["chat"])


@router.post("/api/v1/chat")
async def start_chat(body: ChatRequest, caller: CallerDep, service: Service) -> StreamingResponse:
    return StreamingResponse(
        service.chat(caller, body),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


@router.post("/api/v1/chat/respond/{request_id}")
async def respond_to_hitl(
    request_id: str, body: HitlResponse, caller: CallerDep, service: Service
) -> dict[str, object]:
    return await service.respond_hitl(caller, request_id, body)


@router.post("/api/v1/chat/{thread_id}/cancel", response_model=CancelResponse)
async def cancel_chat(thread_id: str, caller: CallerDep, service: Service) -> CancelResponse:
    return await service.cancel(caller, thread_id)


@router.get("/api/v1/hitl/status/{thread_id}", response_model=HitlStatus)
async def hitl_status(thread_id: str, caller: CallerDep, service: Service) -> HitlStatus:
    return await service.hitl_status(caller, thread_id)


@router.get("/api/v1/approvals", response_model=list[ApprovalOut])
async def list_approvals(caller: CallerDep, service: Service) -> list[ApprovalOut]:
    return await service.list_approvals(caller)
