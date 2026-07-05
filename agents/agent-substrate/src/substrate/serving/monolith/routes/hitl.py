"""HITL response endpoint.

POST /chat/respond/{request_id} – resolve a pending tool-approval
or human-input request from the frontend.

GET /hitl/status/{thread_id} – check for pending HITL requests
(used by the frontend on reconnect to restore approval/input cards).
"""

from __future__ import annotations
from substrate.logger import setup_logging

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.schemas import HITLResponse
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.security.deps import AuthClaims, get_current_user
from substrate.serving.monolith.services import get_owned_thread

logger = setup_logging()

router = APIRouter(
    tags=["hitl"],
    dependencies=[Depends(get_current_user)],
)


@router.post("/chat/respond/{request_id}")
async def respond_to_hitl(
    request_id: str,
    resp: HITLResponse,
    ctx: ServerDependencies = Depends(get_ctx),
):
    """Resolve a pending HITL request (tool approval or human input).

    ``request_id`` is an unguessable UUID minted server-side and delivered
    only over the owner's authenticated SSE stream — it acts as a capability
    token.  Thread-ownership enforcement lands with the durable-signal HITL
    rework (Phase 2), which gives resolution a request→thread mapping.
    """
    data = resp.model_dump(exclude_none=True)
    resolved = await ctx.bridge_registry.resolve(request_id, data)

    if not resolved:
        raise HTTPException(
            status_code=404,
            detail=f"No pending HITL request with id={request_id!r}",
        )

    return {"status": "ok", "request_id": request_id}


@router.get("/hitl/status/{thread_id}")
async def hitl_status(
    thread_id: uuid.UUID,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
):
    """Return pending HITL requests for a thread.

    The frontend calls this on reconnect / page load to check if the agent
    is blocked waiting for user input so it can restore approval or
    human-input cards without re-sending the chat message.

    Returns:
        ``{"pending": [...]}`` — list of pending HITL event payloads.
        Empty list if no HITL is pending.
    """
    if not await get_owned_thread(db, thread_id, user):
        raise HTTPException(status_code=404, detail="Thread not found")

    pending = ctx.bridge_registry.get_pending_hitl(str(thread_id))
    if pending:
        return {"pending": pending}

    # In-memory bridge has nothing (e.g. the monolith process restarted —
    # BridgeRegistry._bridges is not durable, only the run itself is). The
    # run's own suspension IS durable (substrate_run_queue.status='suspended'),
    # and its input.requested card content lives in the EventLog (logged
    # exactly once now via ctx.log_once — see agents/runtime/context.py),
    # so reconstruct the card from there instead of just returning empty.
    pending = await _durable_pending_hitl(ctx, str(thread_id))
    return {"pending": pending}


async def _durable_pending_hitl(ctx: ServerDependencies, thread_id: str) -> list[dict]:
    from substrate.kernel.runtime.ids import RunStatus

    runtime = getattr(ctx, "runtime", None)
    if runtime is None:
        return []
    found = await runtime.scheduler.find_run_for_thread(thread_id)
    if found is None:
        return []
    run_id, status = found
    if status != RunStatus.SUSPENDED:
        return []

    last_request: dict | None = None
    async for entry in runtime.event_log.read(run_id):
        if entry.kind == "input.requested":
            last_request = entry.payload
    if last_request is None:
        return []

    request_id = last_request.get("request_id", "")
    card = {
        "request_id": request_id,
        "run_id": run_id,
        "question": last_request.get("question", ""),
        "context": last_request.get("context", ""),
        "options": last_request.get("options", []),
        "allow_freeform": last_request.get("allow_freeform", True),
    }
    # Rehydrate the in-memory bridge so a subsequent POST /chat/respond
    # resolves through the normal path instead of needing its own lookup.
    bridge = await ctx.bridge_registry.acquire(thread_id)
    bridge.register_signal_request(
        request_id,
        run_id,
        card={k: card[k] for k in ("question", "context", "options", "allow_freeform")},
    )
    return [card]
