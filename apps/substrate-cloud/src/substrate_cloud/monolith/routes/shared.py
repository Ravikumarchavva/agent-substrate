"""Public, read-only view of a shared conversation: ``GET /shared/{token}``. No sign-in; the token is the credential.

Returns what was said (see ``stream.export.messages_of``) and nothing else: no tool calls, reasoning, file contents or user identity.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.database import get_db
from substrate_cloud.monolith.models import ThreadShare
from substrate_cloud.stream import project_thread
from substrate_cloud.stream.export import messages_of

router = APIRouter(prefix="/shared", tags=["shared"])


class SharedMessage(BaseModel):
    role: str
    text: str
    attachments: list[str] = []


class SharedConversation(BaseModel):
    title: Optional[str]
    messages: list[SharedMessage]


@router.get("/{token}", response_model=SharedConversation)
async def get_shared_conversation(
    token: str, request: Request, db: AsyncSession = Depends(get_db)
) -> SharedConversation:
    # Same answer for a token that never existed and one that was revoked: nothing to learn from probing.
    row = await db.execute(select(ThreadShare).where(ThreadShare.token == token))
    share = row.scalars().first()
    runtime = getattr(request.app.state.ctx, "runtime", None)
    if share is None or runtime is None:
        raise HTTPException(status_code=404, detail="This link is not available.")
    events = await project_thread(runtime.store, str(share.thread_id))
    messages: list[dict[str, Any]] = messages_of(
        [e.model_dump(mode="json") for e in events]
    )
    return SharedConversation(
        title=share.name,
        messages=[SharedMessage(**m) for m in messages],
    )
