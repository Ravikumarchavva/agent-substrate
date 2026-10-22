from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from loamz.server.agents.bootstrap.http import CallerDep, use
from loamz.server.agents.features.automation.triggers.model import (
    ConditionOut,
    CreateCondition,
    CreateCronTrigger,
    CreateWebhook,
    CronTriggerOut,
    WebhookOut,
)
from loamz.server.agents.features.automation.triggers.service import TriggersService, triggers

Service = Annotated[TriggersService, Depends(use(triggers))]

router = APIRouter(prefix="/api/v1/triggers", tags=["triggers"])


# ── Cron ──────────────────────────────────────────────────────────────────
@router.get("/cron", response_model=list[CronTriggerOut])
async def list_cron_triggers(caller: CallerDep, service: Service) -> list[CronTriggerOut]:
    return await service.list_cron(caller)


@router.post("/cron", status_code=status.HTTP_201_CREATED)
async def create_cron_trigger(
    body: CreateCronTrigger, caller: CallerDep, service: Service
) -> dict[str, str]:
    return await service.create_cron(caller, body)


@router.delete("/cron/{name}")
async def delete_cron_trigger(name: str, caller: CallerDep, service: Service) -> dict[str, str]:
    return await service.delete_cron(caller, name)


# ── Webhooks ──────────────────────────────────────────────────────────────
@router.get("/webhooks", response_model=list[WebhookOut])
async def list_webhooks(caller: CallerDep, service: Service) -> list[WebhookOut]:
    return await service.list_webhooks(caller)


@router.post("/webhooks", response_model=WebhookOut, status_code=status.HTTP_201_CREATED)
async def create_webhook(body: CreateWebhook, caller: CallerDep, service: Service) -> WebhookOut:
    return await service.create_webhook(caller, body)


@router.delete("/webhooks/{path:path}")
async def delete_webhook(path: str, caller: CallerDep, service: Service) -> dict[str, str]:
    return await service.delete_webhook(caller, path)


@router.post("/webhooks/{path:path}/incoming")
async def handle_webhook(
    path: str, request: Request, caller: CallerDep, service: Service
) -> dict[str, object]:
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    return await service.handle_incoming(caller, path, payload)


# ── Conditions ────────────────────────────────────────────────────────────
@router.get("/conditions", response_model=list[ConditionOut])
async def list_conditions(caller: CallerDep, service: Service) -> list[ConditionOut]:
    return await service.list_conditions(caller)


@router.post("/conditions", status_code=status.HTTP_201_CREATED)
async def create_condition(
    body: CreateCondition, caller: CallerDep, service: Service
) -> dict[str, str]:
    return await service.create_condition(caller, body)


@router.delete("/conditions/{name}")
async def delete_condition(name: str, caller: CallerDep, service: Service) -> dict[str, str]:
    return await service.delete_condition(caller, name)
