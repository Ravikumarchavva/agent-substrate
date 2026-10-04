"""The notification centre: ``GET /notifications`` (newest first, with the unread count) and the calls that mark them read."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from substrate_cloud.monolith.database import get_db
from substrate_cloud.monolith.models import Notification
from substrate_cloud.monolith.security.deps import AuthClaims, get_current_user

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str
    title: str
    body: str
    thread_id: Optional[uuid.UUID] = None
    created_at: datetime
    read_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class NotificationList(BaseModel):
    unread: int
    items: list[NotificationOut]


def _mine(user: AuthClaims):
    # Notifications are not row-secured: the user and tenant on every query are what keep one person's from another's.
    return (
        Notification.tenant_id == (user.tenant_id or "default"),
        Notification.user_identifier == user.sub,
    )


@router.get("", response_model=NotificationList)
async def list_notifications(
    limit: int = Query(30, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
) -> NotificationList:
    rows = await db.execute(
        select(Notification)
        .where(*_mine(user))
        .order_by(Notification.created_at.desc())
        .limit(limit)
    )
    unread = await db.scalar(
        select(func.count())
        .select_from(Notification)
        .where(*_mine(user), Notification.read_at.is_(None))
    )
    return NotificationList(
        unread=int(unread or 0),
        items=[NotificationOut.model_validate(n) for n in rows.scalars().all()],
    )


@router.post("/read-all", status_code=204)
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
) -> None:
    await db.execute(
        update(Notification)
        .where(*_mine(user), Notification.read_at.is_(None))
        .values(read_at=datetime.now(timezone.utc))
    )
    await db.commit()


@router.post("/{notification_id}/read", status_code=204)
async def mark_read(
    notification_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: AuthClaims = Depends(get_current_user),
) -> None:
    done = await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, *_mine(user))
        .values(read_at=datetime.now(timezone.utc))
    )
    if done.rowcount == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    await db.commit()
