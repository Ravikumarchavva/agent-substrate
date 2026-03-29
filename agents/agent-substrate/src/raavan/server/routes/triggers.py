"""Trigger routes — CRUD for cron, webhook, and condition-based triggers."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter(prefix="/triggers", tags=["triggers"])


# ── Request models ────────────────────────────────────────────────────────


class CreateCronTrigger(BaseModel):
    name: str
    schedule: str  # cron expression or interval seconds
    kind: str = "cron"  # "cron" | "interval"
    target_type: str = "pipeline"
    target_name: str = ""
    target_params: dict[str, Any] = {}


class CreateWebhook(BaseModel):
    name: str
    path: str
    target_type: str = "pipeline"
    target_name: str = ""
    target_params: dict[str, Any] = {}


class CreateCondition(BaseModel):
    name: str
    event_type: str
    filters: dict[str, Any] = {}
    target_type: str = "pipeline"
    target_name: str = ""
    target_params: dict[str, Any] = {}


# ── Cron / Interval triggers ─────────────────────────────────────────────


@router.get("/cron")
async def list_cron_triggers(request: Request) -> list[dict[str, Any]]:
    scheduler = _get_scheduler(request)
    return [t.to_dict() for t in scheduler.list_triggers()]


@router.post("/cron")
async def create_cron_trigger(
    body: CreateCronTrigger, request: Request
) -> dict[str, str]:
    from raavan.catalog._triggers.scheduler import TriggerDef

    scheduler = _get_scheduler(request)
    trigger = TriggerDef(
        name=body.name,
        kind=body.kind,  # type: ignore[arg-type]
        schedule=body.schedule,
        target_type=body.target_type,  # type: ignore[arg-type]
        target_name=body.target_name,
        target_params=body.target_params,
    )
    await scheduler.add_trigger(trigger)
    return {"status": "created", "name": body.name}


@router.delete("/cron/{name}")
async def delete_cron_trigger(name: str, request: Request) -> dict[str, str]:
    scheduler = _get_scheduler(request)
    removed = await scheduler.remove_trigger(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Trigger '{name}' not found")
    return {"status": "deleted", "name": name}


# ── Webhooks ──────────────────────────────────────────────────────────────


@router.get("/webhooks")
async def list_webhooks(request: Request) -> list[dict[str, Any]]:
    registry = _get_webhook_registry(request)
    return [w.to_dict() for w in registry.list_webhooks()]


@router.post("/webhooks")
async def create_webhook(body: CreateWebhook, request: Request) -> dict[str, Any]:
    registry = _get_webhook_registry(request)
    webhook = await registry.register(
        name=body.name,
        path=body.path,
        target_type=body.target_type,
        target_name=body.target_name,
        target_params=body.target_params,
    )
    return {"status": "created", **webhook.to_dict()}


@router.delete("/webhooks/{path}")
async def delete_webhook(path: str, request: Request) -> dict[str, str]:
    registry = _get_webhook_registry(request)
    removed = await registry.unregister(path)
    if not removed:
        raise HTTPException(
            status_code=404, detail=f"Webhook at /webhooks/{path} not found"
        )
    return {"status": "deleted", "path": path}


@router.post("/webhooks/{path}/incoming")
async def handle_webhook(path: str, request: Request) -> dict[str, Any]:
    """Receive an incoming webhook payload and dispatch the workflow."""
    registry = _get_webhook_registry(request)
    payload = (
        await request.json()
        if request.headers.get("content-length", "0") != "0"
        else {}
    )
    secret = request.headers.get("x-webhook-secret")
    return await registry.handle(path, payload, secret)


# ── Conditions ────────────────────────────────────────────────────────────


@router.get("/conditions")
async def list_conditions(request: Request) -> list[dict[str, Any]]:
    monitor = _get_condition_monitor(request)
    return [c.to_dict() for c in monitor.list_conditions()]


@router.post("/conditions")
async def create_condition(body: CreateCondition, request: Request) -> dict[str, str]:
    from raavan.catalog._triggers.conditions import ConditionDef

    monitor = _get_condition_monitor(request)
    condition = ConditionDef(
        name=body.name,
        event_type=body.event_type,
        filters=body.filters,
        target_type=body.target_type,
        target_name=body.target_name,
        target_params=body.target_params,
    )
    await monitor.add_condition(condition)
    return {"status": "created", "name": body.name}


@router.delete("/conditions/{name}")
async def delete_condition(name: str, request: Request) -> dict[str, str]:
    monitor = _get_condition_monitor(request)
    removed = await monitor.remove_condition(name)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Condition '{name}' not found")
    return {"status": "deleted", "name": name}


# ── Helpers ───────────────────────────────────────────────────────────────


def _get_scheduler(request: Request) -> Any:
    scheduler = getattr(request.app.state, "trigger_scheduler", None)
    if scheduler is None:
        raise HTTPException(status_code=503, detail="TriggerScheduler not configured")
    return scheduler


def _get_webhook_registry(request: Request) -> Any:
    registry = getattr(request.app.state, "webhook_registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="WebhookRegistry not configured")
    return registry


def _get_condition_monitor(request: Request) -> Any:
    monitor = getattr(request.app.state, "condition_monitor", None)
    if monitor is None:
        raise HTTPException(status_code=503, detail="ConditionMonitor not configured")
    return monitor
