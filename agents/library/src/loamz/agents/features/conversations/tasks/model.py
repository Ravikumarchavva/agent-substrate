from __future__ import annotations

from pydantic import BaseModel, Field


class TaskUpdateRequest(BaseModel):
    status: str | None = None
    title: str | None = None
    note: str | None = None


class AddTasksRequest(BaseModel):
    tasks: list[str] = Field(min_length=1)


class Boards(BaseModel):
    boards: list[dict]


class TaskOut(BaseModel):
    id: str
    title: str
    status: str
    note: str = ""
    retry_count: int | None = None


class TaskResult(BaseModel):
    status: str = "ok"
    task: TaskOut | None = None
    added: int | None = None
