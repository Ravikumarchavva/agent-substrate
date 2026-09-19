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

from pydantic import BaseModel

from substrate.capabilities.storage.workspace import WorkspaceFileStore
from substrate.serving.monolith.security.rls_deps import get_service_scoped_db
from substrate.serving.monolith.dependencies import ServerDependencies, get_ctx
from substrate.serving.monolith.models import Thread, WorkspaceQuota
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
    db: AsyncSession = Depends(get_service_scoped_db),
    _: AuthClaims = Depends(require_admin),
) -> Dict[str, Any]:
    """Return top-level aggregate stats.

    ``total_events`` counts durable EventLogProtocol rows (``event_log``)
    directly — conversation history has no separate steps table anymore; the
    EventLogProtocol is the single source of truth (see ``serving/stream/history.py``).
    """
    thread_count: int = (await db.execute(select(func.count(Thread.id)))).scalar_one()
    event_count: int = (
        await db.execute(text("SELECT COUNT(*) FROM event_log"))
    ).scalar_one()

    return {
        "total_threads": thread_count,
        "total_events": event_count,
    }


@router.get("/threads")
async def list_all_threads(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_service_scoped_db),
    _: AuthClaims = Depends(require_admin),
) -> List[Dict[str, Any]]:
    """Return all threads with EventLogProtocol event counts, newest first.

    Raw SQL (not the ORM) for the event-count join: run_queue and
    event_log are asyncpg-managed tables in the same physical
    database, not SQLAlchemy models, so a plain JOIN is simpler than
    stitching a raw subquery onto ORM Core constructs.
    """
    rows = (
        await db.execute(
            text(
                """
                SELECT
                    t.id, t.name, t.user_identifier, t.created_at, t.updated_at,
                    t.deleted_at,
                    COALESCE(ec.event_count, 0) AS event_count
                FROM threads t
                LEFT JOIN (
                    SELECT rq.thread_id AS thread_id, COUNT(el.*) AS event_count
                    FROM run_queue rq
                    JOIN event_log el ON el.run_id = rq.run_id
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
            "deleted_at": r.deleted_at.isoformat() if r.deleted_at else None,
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
    db: AsyncSession = Depends(get_service_scoped_db),
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


# ── Storage (WorkspaceFileStore only — FILE_STORE_BACKEND=local) ─────────────


def _require_workspace_file_store(ctx: ServerDependencies) -> WorkspaceFileStore:
    store = ctx.file_store
    if not isinstance(store, WorkspaceFileStore):
        raise HTTPException(
            status_code=501,
            detail=(
                "Admin storage management requires the docker-volume backend "
                "(FILE_STORE_BACKEND=local, the default) — not S3/memory."
            ),
        )
    return store


class SetQuotaRequest(BaseModel):
    quota_bytes: int | None  # None resets the tenant to the global default


@router.get("/storage")
async def list_storage_tenants(
    ctx: ServerDependencies = Depends(get_ctx),
    _: AuthClaims = Depends(require_admin),
) -> List[Dict[str, Any]]:
    """Every tenant with a workspace directory, their usage, effective quota
    (override or global default), and conversation count.

    Metered per tenant, not per user: usage/quota stay tenant-scoped even
    though conversation keys do carry a user segment (see
    ``WorkspaceFileStore``'s module docstring) — tenant is the coarser
    identity every key reliably carries.
    """
    store = _require_workspace_file_store(ctx)
    tenants = await store.list_all_tenants()
    result = []
    for tenant_id in tenants:
        used = await store.usage_bytes(tenant_id)
        conversations = await store.list_conversations(tenant_id)
        result.append(
            {
                "tenant_id": tenant_id,
                "used_bytes": used,
                "quota_bytes": store.effective_quota(tenant_id),
                "conversation_count": len(conversations),
            }
        )
    return result


@router.get("/storage/{tenant_id}/conversations")
async def list_storage_conversations(
    tenant_id: str,
    ctx: ServerDependencies = Depends(get_ctx),
    _: AuthClaims = Depends(require_admin),
) -> List[Dict[str, Any]]:
    """``(conversation_id, size_bytes, file_count)`` for one tenant — the
    admin storage page's drill-down. Conversation workspaces are what the
    code interpreter's sandbox mounts (``sandbox_service.py`` /
    ``nsjail.py`` mount ``.../conversations/{cid}/workspace``), so this
    is what an agent's sandbox run actually wrote."""
    store = _require_workspace_file_store(ctx)
    conversations = await store.list_conversations(tenant_id)
    return [
        {"conversation_id": cid, "size_bytes": size, "file_count": count}
        for cid, size, count in conversations
    ]


@router.put("/storage/{tenant_id}/quota")
async def set_storage_quota(
    tenant_id: str,
    body: SetQuotaRequest,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_service_scoped_db),
    _: AuthClaims = Depends(require_admin),
) -> Dict[str, Any]:
    """Set (or, with ``quota_bytes: null``, reset to the global default)
    one tenant's storage quota. Takes effect immediately (updates the live
    store) and persists (upserts/deletes the ``workspace_quotas`` row) so
    it survives a restart."""
    store = _require_workspace_file_store(ctx)

    existing = await db.get(WorkspaceQuota, tenant_id)
    if body.quota_bytes is None:
        if existing is not None:
            await db.delete(existing)
            await db.commit()
    elif existing is not None:
        existing.quota_bytes = body.quota_bytes
        await db.commit()
    else:
        db.add(WorkspaceQuota(user_id=tenant_id, quota_bytes=body.quota_bytes))
        await db.commit()

    store.set_quota_override(tenant_id, body.quota_bytes)
    logger.info("Admin set storage quota for tenant %s: %r", tenant_id, body.quota_bytes)
    return {
        "tenant_id": tenant_id,
        "quota_bytes": store.effective_quota(tenant_id),
    }
