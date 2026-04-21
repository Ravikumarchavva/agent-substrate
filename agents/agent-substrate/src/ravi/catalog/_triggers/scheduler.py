"""Cron/interval trigger scheduler backed by APScheduler + Redis."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

logger = logging.getLogger(__name__)


@dataclass
class TriggerDef:
    """Definition of a scheduled trigger."""

    name: str
    kind: Literal["cron", "interval"]
    schedule: str  # cron expression or interval in seconds
    target_type: Literal["pipeline", "chain", "workflow"]
    target_name: str  # pipeline name or workflow ID template
    target_params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "schedule": self.schedule,
            "target_type": self.target_type,
            "target_name": self.target_name,
            "target_params": self.target_params,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TriggerDef:
        d = dict(data)
        if isinstance(d.get("created_at"), str):
            d["created_at"] = datetime.fromisoformat(d["created_at"])
        return cls(**d)


class TriggerScheduler:
    """APScheduler-based trigger scheduler with Redis job store.

    Manages cron and interval triggers that fire pipelines/chains via Temporal.
    """

    def __init__(self, redis_url: str = "redis://localhost:6379/0") -> None:
        self._redis_url = redis_url
        self._triggers: dict[str, TriggerDef] = {}
        self._scheduler: Any = None
        self._temporal: Any = None  # TemporalClient, set via set_temporal()

    def set_temporal(self, temporal: Any) -> None:
        """Inject the TemporalClient for workflow dispatch."""
        self._temporal = temporal

    async def start(self) -> None:
        """Start the APScheduler background scheduler."""
        from apscheduler import AsyncScheduler
        from apscheduler.datastores.memory import MemoryDataStore

        self._scheduler = AsyncScheduler(data_store=MemoryDataStore())
        await self._scheduler.__aenter__()
        logger.info("TriggerScheduler started")

    async def stop(self) -> None:
        """Stop the scheduler gracefully."""
        if self._scheduler is not None:
            await self._scheduler.__aexit__(None, None, None)
            logger.info("TriggerScheduler stopped")

    async def add_trigger(self, trigger: TriggerDef) -> None:
        """Register a new trigger."""
        if self._scheduler is None:
            raise RuntimeError("Scheduler not started")

        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        if trigger.kind == "cron":
            ap_trigger = CronTrigger.from_crontab(trigger.schedule)
        else:
            ap_trigger = IntervalTrigger(seconds=int(trigger.schedule))

        await self._scheduler.add_schedule(
            self._fire_trigger,
            ap_trigger,
            id=trigger.name,
            args=[trigger.name],
        )

        self._triggers[trigger.name] = trigger
        logger.info(
            "Added trigger '%s' (%s: %s)", trigger.name, trigger.kind, trigger.schedule
        )

    async def remove_trigger(self, name: str) -> bool:
        """Remove a trigger by name."""
        if name not in self._triggers:
            return False

        if self._scheduler is not None:
            try:
                await self._scheduler.remove_schedule(name)
            except Exception:
                logger.warning("Schedule '%s' not found in APScheduler", name)

        del self._triggers[name]
        logger.info("Removed trigger '%s'", name)
        return True

    def list_triggers(self) -> list[TriggerDef]:
        """Return all registered triggers."""
        return list(self._triggers.values())

    def get_trigger(self, name: str) -> TriggerDef | None:
        """Get a trigger by name."""
        return self._triggers.get(name)

    async def _fire_trigger(self, trigger_name: str) -> None:
        """Callback invoked by APScheduler when a trigger fires."""
        trigger = self._triggers.get(trigger_name)
        if trigger is None or not trigger.enabled:
            return

        logger.info(
            "Trigger '%s' fired → %s:%s",
            trigger_name,
            trigger.target_type,
            trigger.target_name,
        )

        if self._temporal is None:
            logger.error(
                "No TemporalClient — cannot dispatch workflow for trigger '%s'",
                trigger_name,
            )
            return

        try:
            if trigger.target_type == "pipeline":
                await self._temporal.start_pipeline_workflow(
                    trigger.target_name,
                    trigger.target_params.get("definition", {}),
                )
            elif trigger.target_type == "chain":
                await self._temporal.start_chain_workflow(
                    trigger.target_params.get("code", ""),
                    trigger.target_params.get("description", ""),
                    timeout=trigger.target_params.get("timeout", 120),
                )
        except Exception:
            logger.exception(
                "Failed to dispatch workflow for trigger '%s'", trigger_name
            )
