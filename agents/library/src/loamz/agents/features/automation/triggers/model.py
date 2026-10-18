from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CreateCronTrigger(BaseModel):
    name: str
    schedule: str
    kind: Literal["cron", "interval"] = "cron"
    target_type: str = "pipeline"
    target_name: str = ""
    target_params: dict[str, object] = Field(default_factory=dict)


class CronTriggerOut(BaseModel):
    name: str
    schedule: str
    kind: str
    target_type: str
    target_name: str
    target_params: dict[str, object] = Field(default_factory=dict)


class CreateWebhook(BaseModel):
    name: str
    path: str
    target_type: str = "pipeline"
    target_name: str = ""
    target_params: dict[str, object] = Field(default_factory=dict)


class WebhookOut(BaseModel):
    name: str
    path: str
    target_type: str
    target_name: str
    target_params: dict[str, object] = Field(default_factory=dict)
    secret: str | None = None


class CreateCondition(BaseModel):
    name: str
    event_type: str
    filters: dict[str, object] = Field(default_factory=dict)
    target_type: str = "pipeline"
    target_name: str = ""
    target_params: dict[str, object] = Field(default_factory=dict)


class ConditionOut(BaseModel):
    name: str
    event_type: str
    filters: dict[str, object] = Field(default_factory=dict)
    target_type: str
    target_name: str
    target_params: dict[str, object] = Field(default_factory=dict)

