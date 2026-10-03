"""``/triggers/*`` share one in-memory scheduler, webhook registry and condition monitor across every tenant, so the routes namespace each
definition under its caller's tenant: a tenant sees, creates and deletes only its own, and a definition always runs as its creator."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from substrate.integrations.triggers.conditions import ConditionMonitor
from substrate.integrations.triggers.webhooks import WebhookRegistry
from substrate_cloud.monolith.routes.triggers import router
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.shared.auth.claims import AuthClaims


class _Scheduler:
    """The ``TriggerScheduler`` surface the routes use, without APScheduler's background task (its cancel scope cannot span a fixture)."""

    def __init__(self) -> None:
        self.triggers: dict = {}

    async def add_trigger(self, trigger) -> None:
        self.triggers[trigger.name] = trigger

    async def remove_trigger(self, name: str) -> bool:
        return self.triggers.pop(name, None) is not None

    def list_triggers(self) -> list:
        return list(self.triggers.values())


@pytest.fixture
def app():
    app = FastAPI()
    app.include_router(router)
    app.state.trigger_scheduler = _Scheduler()
    app.state.webhook_registry = WebhookRegistry()
    app.state.condition_monitor = ConditionMonitor()
    return app


def as_tenant(app: FastAPI, tenant: str) -> AsyncClient:
    app.dependency_overrides[get_current_user] = lambda: AuthClaims(
        sub=f"user-of-{tenant}", tenant_id=tenant
    )
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_one_tenant_never_sees_or_deletes_anothers_cron_triggers(app):
    body = {"name": "nightly", "schedule": "0 3 * * *", "target_name": "report"}
    async with as_tenant(app, "acme") as acme:
        assert (await acme.post("/triggers/cron", json=body)).status_code == 200
        assert [t["name"] for t in (await acme.get("/triggers/cron")).json()] == [
            "nightly"
        ]
    async with as_tenant(app, "globex") as globex:
        assert (await globex.get("/triggers/cron")).json() == []
        assert (await globex.delete("/triggers/cron/nightly")).status_code == 404
        # the same name is free for them: no collision across tenants
        assert (await globex.post("/triggers/cron", json=body)).status_code == 200
    async with as_tenant(app, "acme") as acme:
        assert len((await acme.get("/triggers/cron")).json()) == 1
        assert (await acme.delete("/triggers/cron/nightly")).status_code == 200
        assert (await acme.get("/triggers/cron")).json() == []
    async with as_tenant(app, "globex") as globex:
        assert len((await globex.get("/triggers/cron")).json()) == 1  # theirs survived


async def test_a_definition_always_carries_its_creators_tenant_not_the_requests(app):
    body = {
        "name": "t",
        "schedule": "60",
        "kind": "interval",
        "target_params": {"tenant_id": "someone-else", "x": 1},
    }
    async with as_tenant(app, "acme") as acme:
        await acme.post("/triggers/cron", json=body)
        (row,) = (await acme.get("/triggers/cron")).json()
    assert row["target_params"] == {"tenant_id": "acme", "x": 1}


async def test_webhooks_are_private_to_the_tenant_that_registered_them(app):
    hook = {"name": "orders", "path": "orders", "target_name": "ingest"}
    async with as_tenant(app, "acme") as acme:
        created = (await acme.post("/triggers/webhooks", json=hook)).json()
        assert created["path"] == "orders" and created["name"] == "orders"
        assert [w["path"] for w in (await acme.get("/triggers/webhooks")).json()] == [
            "orders"
        ]
    async with as_tenant(app, "globex") as globex:
        assert (await globex.get("/triggers/webhooks")).json() == []
        assert (await globex.delete("/triggers/webhooks/orders")).status_code == 404
        # globex's request for the same path reaches nothing of acme's
        r = await globex.post("/triggers/webhooks/orders/incoming", json={})
        assert r.status_code != 200 or "acme" not in r.text


async def test_conditions_are_private_to_the_tenant_that_created_them(app):
    cond = {"name": "big-order", "event_type": "order.created", "target_name": "alert"}
    async with as_tenant(app, "acme") as acme:
        assert (await acme.post("/triggers/conditions", json=cond)).status_code == 200
    async with as_tenant(app, "globex") as globex:
        assert (await globex.get("/triggers/conditions")).json() == []
        assert (
            await globex.delete("/triggers/conditions/big-order")
        ).status_code == 404
