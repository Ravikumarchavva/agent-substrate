"""Cancel endpoint — aborts a running agent stream for a given thread.

POST /chat/{thread_id}/cancel
  Sets the cancellation event stored in app.state.cancel_registry so the
  SSE generator in chat.py stops the agent task and yields a "cancelled"
  event back to the frontend.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request

logger = logging.getLogger(__name__)

router = APIRouter(tags=["chat"])


@router.post("/chat/{thread_id}/cancel")
async def cancel_chat(thread_id: str, request: Request):
    """Signal the running agent for *thread_id* to stop.

    Returns ``{"status": "cancelled"}`` if a running stream was found,
    or ``{"status": "not_found"}`` if nothing was active for that thread.
    """
    registry: dict[str, object] = getattr(request.app.state, "cancel_registry", {})
    event = registry.get(thread_id)
    if event is not None:
        event.set()  # type: ignore[attr-defined]
        logger.info("Cancellation requested for thread %s", thread_id)
        return {"status": "cancelled", "thread_id": thread_id}

    logger.debug("Cancel requested for thread %s but no active stream found", thread_id)
    return {"status": "not_found", "thread_id": thread_id}
