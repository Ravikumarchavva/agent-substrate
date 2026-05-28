"""Cancel endpoint — aborts a running agent stream for a given thread.

POST /chat/{thread_id}/cancel
  Sets the cancellation event stored in ctx.cancel_registry so the
  SSE generator in chat.py stops the agent task and yields a "cancelled"
  event back to the frontend.
"""

from __future__ import annotations
from ravi.logger import setup_logging

from fastapi import APIRouter, Depends

from ravi.server.context import ServerContext, get_ctx
from ravi.server.security.deps import get_current_user

logger = setup_logging()

router = APIRouter(
    tags=["chat"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/chat/{thread_id}/cancel")
async def cancel_chat(
    thread_id: str,
    ctx: ServerContext = Depends(get_ctx),
):
    """Signal the running agent for *thread_id* to stop.

    Returns ``{"status": "cancelled"}`` if a running stream was found,
    or ``{"status": "not_found"}`` if nothing was active for that thread.
    """
    event = ctx.cancel_registry.get(thread_id)
    if event is not None:
        event.set()  # type: ignore[union-attr]
        logger.info("Cancellation requested for thread %s", thread_id)
        return {"status": "cancelled", "thread_id": thread_id}

    logger.debug("Cancel requested for thread %s but no active stream found", thread_id)
    return {"status": "not_found", "thread_id": thread_id}
