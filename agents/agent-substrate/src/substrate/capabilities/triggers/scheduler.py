"""Cron/interval trigger scheduler backed by in-process APScheduler.

Single-instance / dev-only for v1: schedules live in an in-memory
APScheduler ``MemoryDataStore`` — nothing durable backs them. This is a
deliberate choice, not an oversight: APScheduler's persistent job stores
(SQLAlchemy, MongoDB) round-trip schedules through a deserializer with a
known, unfixed RCE (PYSEC-2026-282 — see the ``SECURITY_IGNORES`` note in
the Makefile, whose whole justification for ignoring that CVE is "we only
ever construct ``AsyncScheduler(data_store=MemoryDataStore())``"). Moving to
a persistent store to fix the durability gap would reopen that RCE, trading
one problem for a worse one.

Consequences of MemoryDataStore for real deployments:
- A schedule does NOT survive a process restart — it must be re-added by
  whatever created it (e.g. reloaded from Postgres on startup).
- In a multi-replica deployment, every replica runs its OWN independent
  copy of each schedule — a cron trigger fires once PER REPLICA, not once
  total. Do not run more than one replica of a process that calls
  ``TriggerScheduler.start()`` unless you want that.

If this ever needs to be genuinely durable/multi-replica-safe, the CVE
needs a real fix (or a from-scratch trusted-deserializer patch) first —
see ``tests/capabilities/test_triggers.py``'s guardrail test, which fails
loudly if this module's data store ever stops being ``MemoryDataStore``,
since that would silently invalidate the CVE-ignore justification above.
"""

from __future__ import annotations
from substrate.logger import setup_logging

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from apscheduler import AsyncScheduler
    from substrate.agents.runtime import Runtime

logger = setup_logging()


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
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

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
    """In-process APScheduler-based trigger scheduler — see module docstring
    for the single-instance/dev-only durability caveat.

    Manages cron and interval triggers that fire pipelines/chains via native Runtime.
    """

    def __init__(self, runtime: Runtime | None = None) -> None:
        self._triggers: dict[str, TriggerDef] = {}
        self._scheduler: AsyncScheduler | None = None
        self._runtime = runtime

    def set_runtime(self, runtime: Runtime) -> None:
        """Inject active Runtime for trigger dispatch."""
        self._runtime = runtime

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

    # ── Scheduled Tasks Support ──────────────────────────────────────────────

    def set_scheduled_task_executor(
        self, executor_cb: Callable[[uuid.UUID], Awaitable[None]]
    ) -> None:
        """Register the executor callback for scheduled tasks."""
        self._scheduled_task_executor = executor_cb

    async def add_scheduled_task(
        self, task_id: uuid.UUID, cron_expression: str, kind: str = "cron"
    ) -> None:
        """Register/schedule a new persistent scheduled task."""
        if self._scheduler is None:
            raise RuntimeError("Scheduler not started")

        from apscheduler.triggers.cron import CronTrigger
        from apscheduler.triggers.interval import IntervalTrigger

        if kind == "cron":
            ap_trigger = CronTrigger.from_crontab(cron_expression)
        else:
            ap_trigger = IntervalTrigger(seconds=int(cron_expression))

        job_id = f"task_{task_id}"

        # Register fire callback with apscheduler
        await self._scheduler.add_schedule(
            self._fire_scheduled_task,
            ap_trigger,
            id=job_id,
            args=[task_id],
        )
        logger.info(
            "Scheduled task job '%s' registered with schedule: %s (%s)",
            job_id,
            cron_expression,
            kind,
        )

    async def remove_scheduled_task(self, task_id: uuid.UUID) -> bool:
        """Remove a persistent scheduled task job from APScheduler."""
        if self._scheduler is not None:
            try:
                await self._scheduler.remove_schedule(f"task_{task_id}")
                logger.info(
                    "Removed scheduled task job 'task_%s' from APScheduler", task_id
                )
                return True
            except Exception:
                logger.warning(
                    "Scheduled task job 'task_%s' not found in APScheduler", task_id
                )
        return False

    async def get_next_run_time(self, task_id: uuid.UUID) -> datetime | None:
        """Get the next scheduled fire time for a scheduled task."""
        if self._scheduler is not None:
            try:
                schedule = await self._scheduler.get_schedule(f"task_{task_id}")
                if schedule:
                    return schedule.next_fire_time
            except Exception:
                pass
        return None

    async def _fire_scheduled_task(self, task_id: uuid.UUID) -> None:
        """Callback fired by APScheduler to run a scheduled task."""
        logger.info("Scheduler fired scheduled task callback for task_id: %s", task_id)
        if (
            hasattr(self, "_scheduled_task_executor")
            and self._scheduled_task_executor is not None
        ):
            try:
                await self._scheduled_task_executor(task_id)
            except Exception as exc:
                logger.error(
                    "Error executing scheduled task %s callback: %s",
                    task_id,
                    exc,
                    exc_info=True,
                )
        else:
            logger.warning("No executor registered for scheduled task %s", task_id)

    # ── Trigger callbacks ────────────────────────────────────────────────────

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

        if self._runtime is not None:
            from substrate.kernel.core.identity import AgentId
            from substrate.kernel.messaging.message import Message, DataPayload

            agent_id = AgentId(type=trigger.target_type, key=trigger.target_name)
            msg = Message(
                target=agent_id,
                payload=DataPayload(data=trigger.target_params),
            )
            try:
                run_id = await self._runtime.submit(agent_id, msg)
                logger.info(
                    "Trigger '%s' submitted run %s to native runtime for %s",
                    trigger_name,
                    run_id,
                    agent_id,
                )
            except Exception as exc:
                logger.error(
                    "Trigger '%s' failed to submit run for %s: %s",
                    trigger_name,
                    agent_id,
                    exc,
                )
        else:
            logger.warning(
                "Trigger '%s' fired, but no Runtime is configured for dispatch.",
                trigger_name,
            )
