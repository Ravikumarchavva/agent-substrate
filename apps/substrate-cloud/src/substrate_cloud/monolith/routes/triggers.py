"""Trigger routes — CRUD for cron, webhook, and condition-based triggers.

The scheduler, webhook registry and condition monitor are one in-memory object each, shared by every tenant. So every name and path is
stored under the caller's tenant (``{tenant}::{name}``): a caller lists, creates and deletes only their own, two tenants can use the same
name, and a definition's ``target_params`` always carries the tenant that made it (set here, never taken from the request).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.monolith.services import trigger_store
from substrate_cloud.shared.auth.claims import AuthClaims

router = APIRouter(prefix="/triggers", tags=["triggers"])

_SEP = "::"


def _own(tenant: str, name: str) -> str:
    """``name`` as the shared registry stores it: under the tenant, so tenants cannot see or collide with each other."""
    return f"{tenant}{_SEP}{name}"


def _mine(tenant: str, stored: str) -> str | None:
    """The tenant's own name for a stored one, or ``None`` if it belongs to another tenant."""
    prefix = f"{tenant}{_SEP}"
    return stored[len(prefix) :] if stored.startswith(prefix) else None


def _owned(tenant: str, params: dict[str, Any]) -> dict[str, Any]:
    """Request params with the tenant forced to the caller's: a definition can only ever run as its creator."""
    return {**params, "tenant_id": tenant}


def _view(tenant: str, row: dict[str, Any], key: str = "name") -> dict[str, Any] | None:
    own = _mine(tenant, str(row.get(key, "")))
    return None if own is None else {**row, key: own}


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
async def list_cron_triggers(
    request: Request, user: AuthClaims = Depends(get_current_user)
) -> list[dict[str, Any]]:
    scheduler = _get_scheduler(request)
    rows = (_view(user.tenant_id, t.to_dict()) for t in scheduler.list_triggers())
    return [r for r in rows if r is not None]


@router.post("/cron")
async def create_cron_trigger(
    body: CreateCronTrigger,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
) -> dict[str, str]:
    from substrate.integrations.triggers.scheduler import TriggerDef

    scheduler = _get_scheduler(request)
    trigger = TriggerDef(
        name=_own(user.tenant_id, body.name),
        kind=body.kind,  # type: ignore[arg-type]
        schedule=body.schedule,
        target_type=body.target_type,  # type: ignore[arg-type]
        target_name=body.target_name,
        target_params=_owned(user.tenant_id, body.target_params),
    )
    await scheduler.add_trigger(trigger)
    await trigger_store.remember(
        getattr(request.app.state, "session_factory", None),
        trigger_store.CRON,
        trigger.name,
        user.tenant_id,
        {
            "name": trigger.name,
            "kind": trigger.kind,
            "schedule": trigger.schedule,
            "target_type": trigger.target_type,
            "target_name": trigger.target_name,
            "target_params": trigger.target_params,
        },
    )
    return {"status": "created", "name": body.name}


@router.delete("/cron/{name}")
async def delete_cron_trigger(
    name: str, request: Request, user: AuthClaims = Depends(get_current_user)
) -> dict[str, str]:
    scheduler = _get_scheduler(request)
    removed = await scheduler.remove_trigger(_own(user.tenant_id, name))
    if not removed:
        raise HTTPException(status_code=404, detail=f"Trigger '{name}' not found")
    await trigger_store.forget(
        getattr(request.app.state, "session_factory", None), trigger_store.CRON, _own(user.tenant_id, name)
    )
    return {"status": "deleted", "name": name}


# ── Webhooks ──────────────────────────────────────────────────────────────


@router.get("/webhooks")
async def list_webhooks(
    request: Request, user: AuthClaims = Depends(get_current_user)
) -> list[dict[str, Any]]:
    registry = _get_webhook_registry(request)
    rows = (
        _view(user.tenant_id, w.to_dict(), "path") for w in registry.list_webhooks()
    )
    return [r for r in rows if r is not None]


@router.post("/webhooks")
async def create_webhook(
    body: CreateWebhook,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
) -> dict[str, Any]:
    registry = _get_webhook_registry(request)
    webhook = await registry.register(
        name=_own(user.tenant_id, body.name),
        path=_own(user.tenant_id, body.path),
        target_type=body.target_type,
        target_name=body.target_name,
        target_params=_owned(user.tenant_id, body.target_params),
    )
    await trigger_store.remember(
        getattr(request.app.state, "session_factory", None),
        trigger_store.WEBHOOK,
        webhook.path,
        user.tenant_id,
        {
            "name": webhook.name,
            "target_type": webhook.target_type,
            "target_name": webhook.target_name,
            "target_params": webhook.target_params,
            "secret": webhook.secret,
        },
    )
    shown = (
        _view(user.tenant_id, _view(user.tenant_id, webhook.to_dict(), "path") or {})
        or {}
    )
    return {"status": "created", **shown}


@router.delete("/webhooks/{path}")
async def delete_webhook(
    path: str, request: Request, user: AuthClaims = Depends(get_current_user)
) -> dict[str, str]:
    registry = _get_webhook_registry(request)
    removed = await registry.unregister(_own(user.tenant_id, path))
    if not removed:
        raise HTTPException(
            status_code=404, detail=f"Webhook at /webhooks/{path} not found"
        )
    await trigger_store.forget(
        getattr(request.app.state, "session_factory", None),
        trigger_store.WEBHOOK,
        _own(user.tenant_id, path),
    )
    return {"status": "deleted", "path": path}


@router.post("/webhooks/{path}/incoming")
async def handle_webhook(
    path: str, request: Request, user: AuthClaims = Depends(get_current_user)
) -> dict[str, Any]:
    """Receive an incoming webhook payload and dispatch the workflow. Only the caller's own webhook at ``path`` can be reached."""
    registry = _get_webhook_registry(request)
    raw_body = await request.body()
    payload = json.loads(raw_body) if raw_body else {}
    signature = request.headers.get("x-webhook-signature")
    idempotency_key = request.headers.get("x-webhook-idempotency-key")
    return await registry.handle(
        _own(user.tenant_id, path),
        payload,
        raw_body=raw_body,
        signature=signature,
        idempotency_key=idempotency_key,
    )


# ── Conditions ────────────────────────────────────────────────────────────


@router.get("/conditions")
async def list_conditions(
    request: Request, user: AuthClaims = Depends(get_current_user)
) -> list[dict[str, Any]]:
    monitor = _get_condition_monitor(request)
    rows = (_view(user.tenant_id, c.to_dict()) for c in monitor.list_conditions())
    return [r for r in rows if r is not None]


@router.post("/conditions")
async def create_condition(
    body: CreateCondition,
    request: Request,
    user: AuthClaims = Depends(get_current_user),
) -> dict[str, str]:
    from substrate.integrations.triggers.conditions import ConditionDef

    monitor = _get_condition_monitor(request)
    condition = ConditionDef(
        name=_own(user.tenant_id, body.name),
        event_type=body.event_type,
        filters=body.filters,
        target_type=body.target_type,
        target_name=body.target_name,
        target_params=_owned(user.tenant_id, body.target_params),
    )
    await monitor.add_condition(condition)
    await trigger_store.remember(
        getattr(request.app.state, "session_factory", None),
        trigger_store.CONDITION,
        condition.name,
        user.tenant_id,
        {
            "name": condition.name,
            "event_type": condition.event_type,
            "filters": condition.filters,
            "target_type": condition.target_type,
            "target_name": condition.target_name,
            "target_params": condition.target_params,
        },
    )
    return {"status": "created", "name": body.name}


@router.delete("/conditions/{name}")
async def delete_condition(
    name: str, request: Request, user: AuthClaims = Depends(get_current_user)
) -> dict[str, str]:
    monitor = _get_condition_monitor(request)
    removed = await monitor.remove_condition(_own(user.tenant_id, name))
    if not removed:
        raise HTTPException(status_code=404, detail=f"Condition '{name}' not found")
    await trigger_store.forget(
        getattr(request.app.state, "session_factory", None),
        trigger_store.CONDITION,
        _own(user.tenant_id, name),
    )
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
