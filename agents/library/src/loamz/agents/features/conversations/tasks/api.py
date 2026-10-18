from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from loamz.agents.bootstrap.http import CallerDep, use
from loamz.agents.features.conversations.tasks.model import (
    AddTasksRequest,
    Boards,
    TaskResult,
    TaskUpdateRequest,
)
from loamz.agents.features.conversations.tasks.service import TasksService, tasks

Service = Annotated[TasksService, Depends(use(tasks))]

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


@router.get("/{conversation_id}", response_model=Boards)
async def get_tasks(conversation_id: str, caller: CallerDep, service: Service) -> Boards:
    """All agent boards of a conversation."""
    return await service.boards(caller, conversation_id)


@router.post("/{task_list_id}/tasks", response_model=TaskResult)
async def add_tasks(
    task_list_id: str, body: AddTasksRequest, caller: CallerDep, service: Service
) -> TaskResult:
    return await service.add(caller, task_list_id, body)


@router.patch("/{task_list_id}/{task_id}", response_model=TaskResult)
async def update_task(
    task_list_id: str, task_id: str, body: TaskUpdateRequest, caller: CallerDep, service: Service
) -> TaskResult:
    return await service.update(caller, task_list_id, task_id, body)


@router.delete("/{task_list_id}/{task_id}", response_model=TaskResult)
async def delete_task(
    task_list_id: str, task_id: str, caller: CallerDep, service: Service
) -> TaskResult:
    return await service.delete(caller, task_list_id, task_id)


@router.post("/{task_list_id}/{task_id}:retry", response_model=TaskResult)
async def force_retry_task(
    task_list_id: str, task_id: str, caller: CallerDep, service: Service
) -> TaskResult:
    """User override: reset retry_count to 0 and reopen a failed or abandoned task."""
    return await service.retry(caller, task_list_id, task_id)
