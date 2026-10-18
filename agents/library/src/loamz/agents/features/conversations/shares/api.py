from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import Response

from loamz.agents.bootstrap.http import CallerDep, RateLimit, use
from loamz.agents.features.conversations.shares.model import ShareOut, SharedConversation
from loamz.agents.features.conversations.shares.service import SharesService, shares

Service = Annotated[SharesService, Depends(use(shares))]

router = APIRouter(prefix="/api/v1", tags=["shares"])


@router.get("/threads/{thread_id}/share", response_model=ShareOut | None)
async def get_share(thread_id: str, caller: CallerDep, service: Service) -> ShareOut | None:
    """The conversation's public link token, or null if it is not shared."""
    return await service.get(caller, thread_id)


@router.post(
    "/threads/{thread_id}/share", response_model=ShareOut, status_code=status.HTTP_201_CREATED
)
async def create_share(thread_id: str, caller: CallerDep, service: Service) -> ShareOut:
    return await service.create(caller, thread_id)


@router.delete("/threads/{thread_id}/share", status_code=status.HTTP_204_NO_CONTENT)
async def delete_share(thread_id: str, caller: CallerDep, service: Service) -> Response:
    """Stop sharing: the link stops working at once."""
    await service.revoke(caller, thread_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/shared/{token}", response_model=SharedConversation, dependencies=[RateLimit])
async def get_shared_conversation(token: str, service: Service) -> SharedConversation:
    """The public, read-only view of a shared conversation. No sign-in: the token is the credential."""
    return await service.view(token)
