"""Triggers that survive a restart.

The scheduler, webhook registry and condition monitor live in memory, so before this a restart forgot every cron, webhook and condition a user had
made. The routes now write each one here as they register it, and the server registers them all again on start.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from substrate_cloud.monolith.database import system_session
from substrate_cloud.monolith.models import TriggerRecord

logger = logging.getLogger(__name__)

CRON, WEBHOOK, CONDITION = "cron", "webhook", "condition"


async def remember(
    session_factory: async_sessionmaker[AsyncSession] | None,
    kind: str,
    key: str,
    tenant_id: str,
    definition: dict[str, Any],
) -> None:
    """Keep (or replace) the trigger registered under ``key``. With no database (an app assembled without one) triggers stay in memory only."""
    if session_factory is None:
        return
    async with system_session(session_factory) as db:
        await db.execute(
            delete(TriggerRecord).where(
                TriggerRecord.kind == kind, TriggerRecord.key == key
            )
        )
        db.add(
            TriggerRecord(
                kind=kind, key=key, tenant_id=tenant_id, definition=definition
            )
        )
        await db.commit()


async def forget(
    session_factory: async_sessionmaker[AsyncSession] | None, kind: str, key: str
) -> None:
    if session_factory is None:
        return
    async with system_session(session_factory) as db:
        await db.execute(
            delete(TriggerRecord).where(
                TriggerRecord.kind == kind, TriggerRecord.key == key
            )
        )
        await db.commit()


async def restore(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    scheduler: Any,
    webhooks: Any,
    conditions: Any,
) -> int:
    """Register every kept trigger again. One that cannot be registered (a schedule that no longer parses) is skipped and logged, not fatal.
    Returns how many came back."""
    from substrate.integrations.triggers.conditions import ConditionDef
    from substrate.integrations.triggers.scheduler import TriggerDef

    async with system_session(session_factory) as db:
        rows = [
            (r.kind, r.key, dict(r.definition))
            for r in (await db.execute(select(TriggerRecord))).scalars().all()
        ]
    restored = 0
    for kind, key, d in rows:
        try:
            if kind == CRON and scheduler is not None:
                await scheduler.add_trigger(TriggerDef(**d))
            elif kind == WEBHOOK and webhooks is not None:
                hook = await webhooks.register(
                    name=d["name"],
                    path=key,
                    target_type=d["target_type"],
                    target_name=d["target_name"],
                    target_params=d.get("target_params") or {},
                )
                hook.secret = d.get("secret", hook.secret)  # callers already hold it
            elif kind == CONDITION and conditions is not None:
                await conditions.add_condition(ConditionDef(**d))
            else:
                continue
            restored += 1
        except Exception:  # noqa: BLE001
            logger.exception("could not restore %s trigger %s", kind, key)
    return restored
