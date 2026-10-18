"""The notification centre."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from loamz.agents.bootstrap.http import CallerDep, use
from loamz.agents.features.people.notifications.model import NotificationPage
from loamz.agents.features.people.notifications.service import NotificationService, notifications

Service = Annotated[NotificationService, Depends(use(notifications))]

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])


@router.get("", response_model=NotificationPage)
async def list_notifications(
    caller: CallerDep,
    service: Service,
    page_size: Annotated[int, Query(ge=1, le=200)] = 50,
    page_token: str | None = None,
) -> NotificationPage:
    return await service.list(caller, page_size=page_size, page_token=page_token)


@router.post("/{notification_id}:read", status_code=204)
async def mark_read(notification_id: str, caller: CallerDep, service: Service) -> Response:
    await service.mark_read(caller, notification_id)
    return Response(status_code=204)


@router.post(":read-all", status_code=204)
async def mark_all_read(caller: CallerDep, service: Service) -> Response:
    await service.mark_all_read(caller)
    return Response(status_code=204)
