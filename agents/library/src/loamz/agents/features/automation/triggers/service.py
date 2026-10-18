from __future__ import annotations

import secrets
from typing import Annotated

from loamz.agents.bootstrap.container import Container
from loamz.agents.features.automation.triggers.model import (
    ConditionOut,
    CreateCondition,
    CreateCronTrigger,
    CreateWebhook,
    CronTriggerOut,
    WebhookOut,
)
from loamz.agents.features.automation.triggers.store import TriggersStore
from loamz.agents.kernel.caller import Caller
from loamz.data.storage.errors import NotFoundError


class TriggersService:
    def __init__(self, container: Container) -> None:
        self._c = container
        self._db = container.database
        self._store = TriggersStore(self._db)

    async def init(self) -> None:
        await self._store.init()

    # ── Cron ──────────────────────────────────────────────────────────────────
    async def list_cron(self, caller: Caller) -> list[CronTriggerOut]:
        raw = await self._store.list(caller.tenant_id, "cron")
        return [CronTriggerOut(**d) for d in raw]

    async def create_cron(self, caller: Caller, body: CreateCronTrigger) -> dict[str, str]:
        item = CronTriggerOut(
            name=body.name,
            schedule=body.schedule,
            kind=body.kind,
            target_type=body.target_type,
            target_name=body.target_name,
            target_params=body.target_params,
        )
        await self._store.save(caller.tenant_id, "cron", body.name, item.model_dump())
        return {"status": "created", "name": body.name}

    async def delete_cron(self, caller: Caller, name: str) -> dict[str, str]:
        deleted = await self._store.delete(caller.tenant_id, "cron", name)
        if not deleted:
            raise NotFoundError(f"Trigger '{name}' not found")
        return {"status": "deleted", "name": name}

    # ── Webhooks ──────────────────────────────────────────────────────────────
    async def list_webhooks(self, caller: Caller) -> list[WebhookOut]:
        raw = await self._store.list(caller.tenant_id, "webhook")
        return [WebhookOut(**d) for d in raw]

    async def create_webhook(self, caller: Caller, body: CreateWebhook) -> WebhookOut:
        clean_path = body.path.lstrip("/")
        secret = secrets.token_hex(16)
        item = WebhookOut(
            name=body.name,
            path=clean_path,
            target_type=body.target_type,
            target_name=body.target_name,
            target_params=body.target_params,
            secret=secret,
        )
        await self._store.save(
            caller.tenant_id, "webhook", body.name, item.model_dump(), path=clean_path
        )
        return item

    async def delete_webhook(self, caller: Caller, path: str) -> dict[str, str]:
        clean_path = path.lstrip("/")
        deleted = await self._store.delete_by_path(caller.tenant_id, "webhook", clean_path)
        if not deleted:
            raise NotFoundError(f"Webhook '{clean_path}' not found")
        return {"status": "deleted", "path": clean_path}

    async def handle_incoming(
        self,
        caller: Caller,
        path: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        clean_path = path.lstrip("/")
        wh = await self._store.get_by_path(caller.tenant_id, "webhook", clean_path)
        if wh is None:
            raise NotFoundError(f"Webhook '{clean_path}' not found")
        return {
            "status": "handled",
            "webhook": clean_path,
            "target_type": wh.get("target_type", "pipeline"),
            "target_name": wh.get("target_name", ""),
        }

    # ── Conditions ────────────────────────────────────────────────────────────
    async def list_conditions(self, caller: Caller) -> list[ConditionOut]:
        raw = await self._store.list(caller.tenant_id, "condition")
        return [ConditionOut(**d) for d in raw]

    async def create_condition(self, caller: Caller, body: CreateCondition) -> dict[str, str]:
        item = ConditionOut(
            name=body.name,
            event_type=body.event_type,
            filters=body.filters,
            target_type=body.target_type,
            target_name=body.target_name,
            target_params=body.target_params,
        )
        await self._store.save(caller.tenant_id, "condition", body.name, item.model_dump())
        return {"status": "created", "name": body.name}

    async def delete_condition(self, caller: Caller, name: str) -> dict[str, str]:
        deleted = await self._store.delete(caller.tenant_id, "condition", name)
        if not deleted:
            raise NotFoundError(f"Condition '{name}' not found")
        return {"status": "deleted", "name": name}


def triggers(c: Container) -> TriggersService:
    return TriggersService(c)


TriggersServiceDep = Annotated[TriggersService, None]
