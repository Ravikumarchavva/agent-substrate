"""Admin routes – accessible only to users with the admin role.

All endpoints require a valid JWT access token where ``role`` is
``"platform_admin"`` or ``"tenant_admin"`` (i.e. ``AuthClaims.is_admin``
returns True).
"""

from __future__ import annotations

import logging

import uuid
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import BaseModel

from substrate.stores.file_tables import Files
from substrate_cloud.monolith.security.rls_deps import get_service_scoped_db
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.models import Thread
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.storage_admin import (
    DataStorageAdmin,
    LocalStorageAdmin,
    StorageAdmin,
    StorageUnsupported,
)
from substrate_cloud.stream import project_thread

logger = logging.getLogger(__name__)

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
    EventLogProtocol is the single source of truth (see ``substrate_cloud/stream/history.py``).
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

    events = await project_thread(runtime.store, thread_id)
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


# ── Storage ──────────────────────────────────────────────────────────────────


def _storage_admin(ctx: ServerDependencies) -> StorageAdmin:
    """The storage page's backend: the data service as an administrator, or the local folder store."""
    access = ctx.file_access
    if access is not None and access.data_backed:
        if access.admin is None:
            raise HTTPException(
                status_code=501,
                detail="Admin storage management needs DATA_ADMIN_OIDC_ISSUER, DATA_ADMIN_CLIENT_ID and DATA_ADMIN_CLIENT_SECRET.",
            )
        return DataStorageAdmin(access.admin, access.connection.volume_of)
    if isinstance(ctx.file_store, Files):
        return LocalStorageAdmin(ctx.file_store)
    raise HTTPException(
        status_code=501,
        detail="Admin storage management needs the data service or the store's own file folder.",
    )


class SetQuotaRequest(BaseModel):
    quota_bytes: int | None  # None removes the tenant volume's quota


@router.get("/storage")
async def list_storage_tenants(
    ctx: ServerDependencies = Depends(get_ctx),
    _: AuthClaims = Depends(require_admin),
) -> List[Dict[str, Any]]:
    """Every tenant with a workspace, their usage, quota and conversation count.

    Metered per tenant, not per user: usage/quota stay tenant-scoped even though conversation keys do carry a user segment (see
    ``Files``' module docstring) — tenant is the coarser identity every key reliably carries.
    """
    return await _storage_admin(ctx).tenants()


@router.get("/storage/{tenant_id}/conversations")
async def list_storage_conversations(
    tenant_id: str,
    ctx: ServerDependencies = Depends(get_ctx),
    _: AuthClaims = Depends(require_admin),
) -> List[Dict[str, Any]]:
    """``(conversation_id, size_bytes, file_count)`` for one tenant — the admin storage page's drill-down. Conversation workspaces
    are what the code interpreter's sandbox mounts (``sandbox_service.py`` / ``nsjail.py`` mount ``.../conversations/{cid}/workspace``),
    so this is what an agent's sandbox run actually wrote."""
    conversations = await _storage_admin(ctx).conversations(tenant_id)
    return [
        {"conversation_id": cid, "size_bytes": size, "file_count": count}
        for cid, size, count in conversations
    ]


@router.put("/storage/{tenant_id}/quota")
async def set_storage_quota(
    tenant_id: str,
    body: SetQuotaRequest,
    ctx: ServerDependencies = Depends(get_ctx),
    _: AuthClaims = Depends(require_admin),
) -> Dict[str, Any]:
    """Set (or, with ``quota_bytes: null``, remove) one tenant's storage quota. It is the tenant volume's hard quota in the data
    service, enforced there on every write; this process keeps no copy."""
    try:
        result = await _storage_admin(ctx).set_quota(tenant_id, body.quota_bytes)
    except StorageUnsupported as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    logger.info(
        "Admin set storage quota for tenant %s: %r", tenant_id, body.quota_bytes
    )
    return result
