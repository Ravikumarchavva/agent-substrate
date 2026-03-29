"""Condition-based triggers — monitor EventBus streams and fire workflows."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ConditionDef:
    """Definition of a condition-based trigger."""

    name: str
    event_type: str  # EventBus event type to watch for
    filters: dict[str, Any] = field(
        default_factory=dict
    )  # key-value match on event data
    target_type: str = "pipeline"
    target_name: str = ""
    target_params: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "event_type": self.event_type,
            "filters": self.filters,
            "target_type": self.target_type,
            "target_name": self.target_name,
            "target_params": self.target_params,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat(),
        }

    def matches(self, event: dict[str, Any]) -> bool:
        """Check if an event matches this condition's filters."""
        if event.get("type") != self.event_type:
            return False
        data = event.get("data", {})
        return all(data.get(k) == v for k, v in self.filters.items())


class ConditionMonitor:
    """Monitors the EventBus for events matching registered conditions.

    When a matching event is detected, dispatches the configured workflow
    via Temporal.
    """

    def __init__(self) -> None:
        self._conditions: dict[str, ConditionDef] = {}
        self._temporal: Any = None
        self._event_bus: Any = None
        self._task: asyncio.Task[None] | None = None

    def set_temporal(self, temporal: Any) -> None:
        self._temporal = temporal

    def set_event_bus(self, bus: Any) -> None:
        self._event_bus = bus

    async def start(self) -> None:
        """Start the background monitoring task."""
        if self._event_bus is None:
            logger.warning("ConditionMonitor: no EventBus configured, skipping start")
            return

        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("ConditionMonitor started")

    async def stop(self) -> None:
        """Stop the monitoring task."""
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            logger.info("ConditionMonitor stopped")

    async def add_condition(self, condition: ConditionDef) -> None:
        """Register a new condition trigger."""
        self._conditions[condition.name] = condition
        logger.info(
            "Added condition '%s' (event_type=%s)", condition.name, condition.event_type
        )

    async def remove_condition(self, name: str) -> bool:
        """Remove a condition by name."""
        if name not in self._conditions:
            return False
        del self._conditions[name]
        logger.info("Removed condition '%s'", name)
        return True

    def list_conditions(self) -> list[ConditionDef]:
        """Return all registered conditions."""
        return list(self._conditions.values())

    async def _monitor_loop(self) -> None:
        """Subscribe to EventBus and check events against conditions."""
        channel = "agent-framework:events"

        try:
            async for event in self._event_bus.subscribe(channel):
                if not isinstance(event, dict):
                    continue

                for condition in self._conditions.values():
                    if not condition.enabled:
                        continue
                    if condition.matches(event):
                        await self._dispatch(condition, event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("ConditionMonitor loop error")

    async def _dispatch(self, condition: ConditionDef, event: dict[str, Any]) -> None:
        """Dispatch a workflow when condition is met."""
        logger.info(
            "Condition '%s' matched event %s → %s:%s",
            condition.name,
            event.get("type"),
            condition.target_type,
            condition.target_name,
        )

        if self._temporal is None:
            logger.error("No TemporalClient for condition '%s'", condition.name)
            return

        try:
            params = {**condition.target_params, "trigger_event": event}
            if condition.target_type == "pipeline":
                await self._temporal.start_pipeline_workflow(
                    condition.target_name,
                    params.get("definition", {}),
                )
            elif condition.target_type == "chain":
                await self._temporal.start_chain_workflow(
                    params.get("code", ""),
                    params.get("description", ""),
                )
        except Exception:
            logger.exception("Condition dispatch failed for '%s'", condition.name)
