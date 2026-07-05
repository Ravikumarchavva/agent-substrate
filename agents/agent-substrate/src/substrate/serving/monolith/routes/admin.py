"""Admin routes – accessible only to users with the admin role.

All endpoints require a valid JWT access token where ``role`` is
``"platform_admin"`` or ``"tenant_admin"`` (i.e. ``AuthClaims.is_admin``
returns True).
"""

from __future__ import annotations
from substrate.logger import setup_logging

import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.serving.monolith.database import get_db
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.models import Thread
from substrate.serving.monolith.security.deps import AuthClaims, get_current_user
from substrate.serving.stream import project_thread

logger = setup_logging()

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Auth guard ───────────────────────────────────────────────────────────────


def require_admin(
    current_user: AuthClaims = Depends(get_current_user),
) -> AuthClaims:
    """Raise 403 unless the authenticated user has an admin role."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden: admin access only")
    return current_user


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/stats")
async def admin_stats(
    db: AsyncSession = Depends(get_db),
    _: AuthClaims = Depends(require_admin),
) -> Dict[str, Any]:
    """Return top-level aggregate stats.

    ``total_events`` counts durable EventLog rows (``substrate_event_log``)
    directly — conversation history has no separate steps table anymore; the
    EventLog is the single source of truth (see ``serving/stream/history.py``).
    """
    thread_count: int = (await db.execute(select(func.count(Thread.id)))).scalar_one()
    event_count: int = (
        await db.execute(text("SELECT COUNT(*) FROM substrate_event_log"))
    ).scalar_one()

    return {
        "total_threads": thread_count,
        "total_events": event_count,
    }


@router.get("/threads")
async def list_all_threads(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: AuthClaims = Depends(require_admin),
) -> List[Dict[str, Any]]:
    """Return all threads with EventLog event counts, newest first.

    Raw SQL (not the ORM) for the event-count join: substrate_run_queue and
    substrate_event_log are asyncpg-managed tables in the same physical
    database, not SQLAlchemy models, so a plain JOIN is simpler than
    stitching a raw subquery onto ORM Core constructs.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT
                    t.id, t.name, t.user_identifier, t.created_at, t.updated_at,
                    COALESCE(ec.event_count, 0) AS event_count
                FROM threads t
                LEFT JOIN (
                    SELECT rq.thread_id AS thread_id, COUNT(el.*) AS event_count
                    FROM substrate_run_queue rq
                    JOIN substrate_event_log el ON el.run_id = rq.run_id
                    WHERE rq.thread_id IS NOT NULL
                    GROUP BY rq.thread_id
                ) ec ON ec.thread_id = t.id::text
                ORDER BY t.updated_at DESC
                OFFSET :skip LIMIT :limit
                """
            ),
            {"skip": skip, "limit": limit},
        )
    ).all()
    return [
        {
            "id": str(r.id),
            "name": r.name or "Untitled",
            "user_identifier": r.user_identifier,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "event_count": r.event_count,
        }
        for r in rows
    ]


@router.get("/threads/{thread_id}/steps")
async def get_thread_steps(
    thread_id: str,
    ctx: ServerDependencies = Depends(get_ctx),
    _: AuthClaims = Depends(require_admin),
) -> List[Dict[str, Any]]:
    """Return the thread's full wire-event history (for admin inspection).

    Same projection the user-facing history endpoint and live streaming use
    (``project_thread()``) — there is no separate admin-only steps table.
    """
    try:
        uuid.UUID(thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid thread ID") from exc

    runtime = ctx.runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Runtime not configured")

    events = await project_thread(runtime.event_log, runtime.scheduler, thread_id)
    return [event.model_dump(mode="json") for event in events]


@router.delete("/threads/{thread_id}")
async def delete_thread(
    thread_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: AuthClaims = Depends(require_admin),
) -> Dict[str, str]:
    """Hard-delete a thread and all its steps (admin only)."""

    try:
        tid = uuid.UUID(thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid thread ID") from exc

    thread = (
        await db.execute(select(Thread).where(Thread.id == tid))
    ).scalar_one_or_none()

    if not thread:
        raise HTTPException(status_code=404, detail="Thread not found")

    await db.delete(thread)
    await db.commit()
    logger.info("Admin deleted thread %s", thread_id)
    return {"deleted": thread_id}
