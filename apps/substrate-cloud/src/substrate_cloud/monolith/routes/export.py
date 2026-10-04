"""``GET /me/export``: everything the account holds about the caller, as one JSON download (the "download my data" right).

Their conversations (what was said, with the files attached by name), memories, preferences, scheduled tasks and notifications. File
contents are not included: they are in Storage, where they can be downloaded one by one. Permanent erasure is a separate, deliberate action
(``routes/gdpr.py``); this only reads.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from substrate.stores import MemoryNamespace, MemoryQuery
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.models import (
    Notification,
    ScheduledTask,
    Thread,
    UserPreferences,
)
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user
from substrate_cloud.monolith.security.rls_deps import get_tenant_scoped_db
from substrate_cloud.stream import project_thread
from substrate_cloud.stream.export import messages_of

router = APIRouter(prefix="/me/export", tags=["export"])

MAX_THREADS = 500
MIN_GAP_S = 60.0
_last: dict[str, float] = {}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@router.get("")
async def export_my_data(
    request: Request,
    ctx: ServerDependencies = Depends(get_ctx),
    db: AsyncSession = Depends(get_tenant_scoped_db),
    user: AuthClaims = Depends(get_current_user),
) -> Response:
    # Building this reads every conversation: once a minute per person is plenty and keeps it from being used to load the server.
    now = time.monotonic()
    if now - _last.get(user.sub, -MIN_GAP_S) < MIN_GAP_S:
        raise HTTPException(
            status_code=429,
            detail="Your data was just exported. Try again in a minute.",
            headers={"Retry-After": str(int(MIN_GAP_S))},
        )
    _last[user.sub] = now

    threads = list(
        (
            await db.execute(
                select(Thread)
                .where(Thread.user_identifier == user.sub, Thread.deleted_at.is_(None))
                .order_by(Thread.created_at)
                .limit(MAX_THREADS)
            )
        ).scalars()
    )
    runtime = getattr(ctx, "runtime", None)
    conversations = []
    for t in threads:
        events = (
            await project_thread(runtime.store, str(t.id))
            if runtime is not None
            else []
        )
        conversations.append(
            {
                "id": str(t.id),
                "title": t.name,
                "created_at": _iso(t.created_at),
                "archived": t.archived_at is not None,
                "messages": messages_of([e.model_dump(mode="json") for e in events]),
            }
        )

    memories = []
    if ctx.long_term_memory is not None:
        namespace = MemoryNamespace(
            tenant_id=user.tenant_id or "default", user_id=user.sub
        )
        found = await ctx.long_term_memory.query(
            MemoryQuery(namespace=namespace, limit=500)
        )
        memories = [m.text for m in found if m.record.namespace.user_id == user.sub]

    prefs = (
        (
            await db.execute(
                select(UserPreferences).where(
                    UserPreferences.tenant_id == (user.tenant_id or "default"),
                    UserPreferences.user_identifier == user.sub,
                )
            )
        )
        .scalars()
        .first()
    )
    tasks = (
        list(
            (
                await db.execute(
                    select(ScheduledTask).where(
                        ScheduledTask.thread_id.in_([t.id for t in threads])
                    )
                )
            ).scalars()
        )
        if threads
        else []
    )
    notes = list(
        (
            await db.execute(
                select(Notification)
                .where(
                    Notification.tenant_id == (user.tenant_id or "default"),
                    Notification.user_identifier == user.sub,
                )
                .order_by(Notification.created_at.desc())
                .limit(200)
            )
        ).scalars()
    )

    document = {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "account": {"id": user.sub, "email": user.email or None},
        "preferences": {
            "custom_instructions": prefs.custom_instructions if prefs else None,
            "timezone": prefs.timezone if prefs else None,
            "models": prefs.models if prefs else {},
        },
        "memories": memories,
        "conversations": conversations,
        "scheduled_tasks": [
            {
                "name": s.name,
                "prompt": s.prompt,
                "schedule": s.cron_expression,
                "kind": s.kind,
                "status": s.status,
                "created_at": _iso(s.created_at),
            }
            for s in tasks
        ],
        "notifications": [
            {"title": n.title, "body": n.body, "at": _iso(n.created_at)} for n in notes
        ],
        "note": "File contents are not included; download them from Storage.",
    }
    return Response(
        content=json.dumps(document, indent=2),
        media_type="application/json",
        headers={
            "Content-Disposition": 'attachment; filename="my-data.json"',
            "Cache-Control": "no-store",
        },
    )
