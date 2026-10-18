from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from loamz.agents.bootstrap.http import CallerDep, use
from loamz.agents.features.automation.schedules.model import (
    ScheduledTaskCreate,
    ScheduledTaskFeedbackRequest,
    ScheduledTaskOut,
    ScheduledTaskParseRequest,
    ScheduledTaskParseResponse,
    ScheduledTaskRunOut,
    ScheduledTaskUpdate,
)
from loamz.agents.features.automation.schedules.service import SchedulesService, schedules

Service = Annotated[SchedulesService, Depends(use(schedules))]

router = APIRouter(prefix="/api/v1/scheduled", tags=["scheduled"])


@router.get("", response_model=list[ScheduledTaskOut])
async def list_scheduled_tasks(caller: CallerDep, service: Service) -> list[ScheduledTaskOut]:
    return await service.list(caller)


@router.post("", response_model=ScheduledTaskOut, status_code=status.HTTP_201_CREATED)
async def create_scheduled_task_endpoint(
    body: ScheduledTaskCreate, caller: CallerDep, service: Service
) -> ScheduledTaskOut:
    return await service.create(caller, body)


@router.post("/parse", response_model=ScheduledTaskParseResponse)
async def parse_schedule_endpoint(
    body: ScheduledTaskParseRequest, service: Service
) -> ScheduledTaskParseResponse:
    return service.parse(body.text)


@router.get("/{task_id}", response_model=ScheduledTaskOut)
async def get_scheduled_task(task_id: str, caller: CallerDep, service: Service) -> ScheduledTaskOut:
    return await service.get(caller, task_id)


@router.patch("/{task_id}", response_model=ScheduledTaskOut)
async def update_scheduled_task_endpoint(
    task_id: str, body: ScheduledTaskUpdate, caller: CallerDep, service: Service
) -> ScheduledTaskOut:
    return await service.patch(caller, task_id, body)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_scheduled_task_endpoint(task_id: str, caller: CallerDep, service: Service) -> None:
    await service.delete(caller, task_id)


@router.post("/{task_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def trigger_scheduled_task_now(
    task_id: str, caller: CallerDep, service: Service
) -> dict[str, str]:
    await service.run_now(caller, task_id)
    return {"status": "triggered"}


@router.get("/{task_id}/runs", response_model=list[ScheduledTaskRunOut])
async def list_scheduled_task_runs(
    task_id: str,
    caller: CallerDep,
    service: Service,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    include_silent: bool = Query(True),
) -> list[ScheduledTaskRunOut]:
    return await service.list_runs(
        caller, task_id, limit=limit, offset=offset, include_silent=include_silent
    )


@router.post("/{task_id}/feedback", status_code=status.HTTP_201_CREATED)
async def add_scheduled_task_feedback(
    task_id: str,
    body: ScheduledTaskFeedbackRequest,
    caller: CallerDep,
    service: Service,
) -> dict[str, str]:
    await service.add_feedback(caller, task_id, body.content)
    return {"status": "success"}
