from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ScheduledTaskCreate(BaseModel):
    name: str
    prompt: str
    cron_expression: str
    kind: Literal["cron", "interval"] = "cron"
    task_type: Literal["report", "monitor", "reminder", "learning"] = "report"
    lookback_runs: int = 5
    auto_disable: bool = False
    email_results: bool = False
    ask_before_acting: bool = True


class ScheduledTaskUpdate(BaseModel):
    name: str | None = None
    prompt: str | None = None
    cron_expression: str | None = None
    kind: Literal["cron", "interval"] | None = None
    status: Literal["active", "paused", "completed", "error"] | None = None
    lookback_runs: int | None = None
    auto_disable: bool | None = None
    email_results: bool | None = None
    ask_before_acting: bool | None = None


class ScheduledTaskRunOut(BaseModel):
    id: str
    task_id: str
    status: str
    output_summary: str = ""
    executed_at: float
    duration_ms: int = 0
    was_silent: bool = False
    error_message: str | None = None
    tokens: int = 0
    cost_usd: float = 0.0


class ScheduledTaskOut(BaseModel):
    id: str
    user_id: str | None = None
    name: str
    prompt: str
    cron_expression: str
    kind: str
    thread_id: str
    status: str
    lookback_runs: int
    task_type: str
    auto_disable: bool
    email_results: bool = False
    ask_before_acting: bool = True
    created_at: float
    updated_at: float
    last_run_at: float | None = None
    next_run_at: float | None = None
    recent_runs: list[ScheduledTaskRunOut] = Field(default_factory=list)


class ScheduledTaskParseRequest(BaseModel):
    text: str


class ScheduledTaskParseResponse(BaseModel):
    name: str
    prompt: str
    cron_expression: str
    kind: Literal["cron", "interval"]
    task_type: Literal["report", "monitor", "reminder", "learning"]


class ScheduledTaskFeedbackRequest(BaseModel):
    content: str

