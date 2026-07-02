"""Cancel endpoint — aborts a running agent stream for a given thread.

POST /chat/{thread_id}/cancel
  Sets the cancellation event stored in ctx.cancel_registry so the
  SSE generator in chat.py stops the agent task and yields a "cancelled"
  event back to the frontend.
"""

from __future__ import annotations
from substrate.logger import setup_logging

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.security.deps import AuthClaims, get_current_user
from substrate.serving.monolith.services import get_owned_thread

logger = setup_logging()

router = APIRouter(
    tags=["chat"],
)


@router.post("/chat/{thread_id}/cancel")
async def cancel_chat(
    thread_id: uuid.UUID,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Signal the running agent for *thread_id* to stop.

    Returns ``{"status": "cancelled"}`` if a running stream was found,
    or ``{"status": "not_found"}`` if nothing was active for that thread.
    """
    if not await get_owned_thread(db, thread_id, user):
        raise HTTPException(status_code=404, detail="Thread not found")

    event = ctx.cancel_registry.get(str(thread_id))
    if event is not None:
        event.set()  # type: ignore[union-attr]
        logger.info("Cancellation requested for thread %s", thread_id)
        return {"status": "cancelled", "thread_id": str(thread_id)}

    logger.debug("Cancel requested for thread %s but no active stream found", thread_id)
    return {"status": "not_found", "thread_id": str(thread_id)}
