"""Admin routes – accessible only to users with the admin role.

All endpoints require a valid JWT access token where ``role`` is
``"platform_admin"`` or ``"tenant_admin"`` (i.e. ``AuthClaims.is_admin``
returns True).
"""

from __future__ import annotations
from ravi.logger import setup_logging

import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ravi.serving.monolith.database import get_db
from ravi.serving.monolith.models import Step, Thread
from ravi.serving.monolith.security.deps import TokenPayload, get_current_user
logger = setup_logging()

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Auth guard ───────────────────────────────────────────────────────────────


def require_admin(
    current_user: TokenPayload = Depends(get_current_user),
) -> TokenPayload:
    """Raise 403 unless the authenticated user has an admin role."""
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden: admin access only")
    return current_user


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.get("/stats")
async def admin_stats(
    db: AsyncSession = Depends(get_db),
    _: TokenPayload = Depends(require_admin),
) -> Dict[str, Any]:
    """Return top-level aggregate stats."""

    thread_count: int = (await db.execute(select(func.count(Thread.id)))).scalar_one()
    step_count: int = (await db.execute(select(func.count(Step.id)))).scalar_one()

    return {
        "total_threads": thread_count,
        "total_steps": step_count,
    }


@router.get("/threads")
async def list_all_threads(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    _: TokenPayload = Depends(require_admin),
) -> List[Dict[str, Any]]:
    """Return all threads with step counts, newest first."""

    # Sub-query: count of steps per thread
    step_counts_sq = (
        select(Step.thread_id, func.count(Step.id).label("step_count"))
        .group_by(Step.thread_id)
        .subquery()
    )

    q = (
        select(
            Thread.id,
            Thread.name,
            Thread.user_identifier,
            Thread.created_at,
            Thread.updated_at,
            func.coalesce(step_counts_sq.c.step_count, 0).label("step_count"),
        )
        .outerjoin(step_counts_sq, Thread.id == step_counts_sq.c.thread_id)
        .order_by(desc(Thread.updated_at))
        .offset(skip)
        .limit(limit)
    )

    rows = (await db.execute(q)).all()
    return [
        {
            "id": str(r.id),
            "name": r.name or "Untitled",
            "user_identifier": r.user_identifier,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "step_count": r.step_count,
        }
        for r in rows
    ]


@router.get("/threads/{thread_id}/steps")
async def get_thread_steps(
    thread_id: str,
    db: AsyncSession = Depends(get_db),
    _: TokenPayload = Depends(require_admin),
) -> List[Dict[str, Any]]:
    """Return all steps for a specific thread (for admin inspection)."""

    try:
        tid = uuid.UUID(thread_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid thread ID") from exc

    q = select(Step).where(Step.thread_id == tid).order_by(Step.created_at)
    steps = (await db.execute(q)).scalars().all()

    return [
        {
            "id": str(s.id),
            "type": s.type,
            "name": s.name,
            "input": s.input,
            "output": s.output,
            "is_error": s.is_error,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        }
        for s in steps
    ]


@router.delete("/threads/{thread_id}")
async def delete_thread(
    thread_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: TokenPayload = Depends(require_admin),
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
