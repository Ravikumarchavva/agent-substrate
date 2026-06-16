"""Condition-based triggers — monitor EventBus streams and fire workflows."""

from __future__ import annotations
from ravi.logger import setup_logging

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ravi.agents.runtime import Runtime
    from ravi.integrations.events.redis_event_bus import EventBus

logger = setup_logging()


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
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

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
    via native Runtime.
    """

    def __init__(self, runtime: Runtime | None = None) -> None:
        self._conditions: dict[str, ConditionDef] = {}
        self._event_bus: EventBus | None = None
        self._runtime = runtime
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running: bool = False

    def set_event_bus(self, bus: EventBus) -> None:
        self._event_bus = bus

    def set_runtime(self, runtime: Runtime) -> None:
        """Inject active Runtime for trigger dispatch."""
        self._runtime = runtime

    async def start(self) -> None:
        """Start the background monitoring tasks for registered conditions."""
        if self._event_bus is None:
            logger.warning("ConditionMonitor: no EventBus configured, skipping start")
            return

        try:
            await self._event_bus.connect()
        except Exception as exc:
            logger.error("ConditionMonitor failed to connect to EventBus: %s", exc)
            return

        self._running = True
        # Start subscription task for each unique event type in conditions
        event_types = {c.event_type for c in self._conditions.values()}
        for et in event_types:
            await self._start_monitoring(et)
        logger.info("ConditionMonitor started")

    async def stop(self) -> None:
        """Stop all background monitoring tasks and disconnect EventBus."""
        self._running = False
        for task in self._tasks.values():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()

        if self._event_bus is not None:
            try:
                await self._event_bus.disconnect()
            except Exception as exc:
                logger.warning(
                    "ConditionMonitor failed to disconnect EventBus: %s", exc
                )

        logger.info("ConditionMonitor stopped")

    async def add_condition(self, condition: ConditionDef) -> None:
        """Register a new condition trigger."""
        self._conditions[condition.name] = condition
        logger.info(
            "Added condition '%s' (event_type=%s)", condition.name, condition.event_type
        )
        if self._running and self._event_bus is not None:
            await self._start_monitoring(condition.event_type)

    async def remove_condition(self, name: str) -> bool:
        """Remove a condition by name."""
        if name not in self._conditions:
            return False
        del self._conditions[name]
        logger.info("Removed condition '%s'", name)
        # Note: we keep the subscription loop running even if last condition is removed,
        # to simplify lifecycle management.
        return True

    def list_conditions(self) -> list[ConditionDef]:
        """Return all registered conditions."""
        return list(self._conditions.values())

    async def _start_monitoring(self, event_type: str) -> None:
        """Spawn a subscription loop for a specific event type if not already monitoring."""
        if event_type in self._tasks:
            return
        self._tasks[event_type] = asyncio.create_task(
            self._monitor_event_type_loop(event_type)
        )

    async def _monitor_event_type_loop(self, event_type: str) -> None:
        """Subscribe to specific event_type stream and match conditions."""
        try:
            async for envelope in self._event_bus.subscribe(
                event_type, "condition-monitor"
            ):
                event_dict = {
                    "type": envelope.event_type,
                    "data": envelope.payload,
                }
                # Create a snapshot copy of conditions to avoid dict mutation during iteration
                for condition in list(self._conditions.values()):
                    if not condition.enabled or condition.event_type != event_type:
                        continue
                    if condition.matches(event_dict):
                        await self._dispatch(condition, event_dict)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "ConditionMonitor loop error for event type %s", event_type
            )

    async def _dispatch(self, condition: ConditionDef, event: dict[str, Any]) -> None:
        """Dispatch a workflow when condition is met."""
        logger.info(
            "Condition '%s' matched event %s → %s:%s",
            condition.name,
            event.get("type"),
            condition.target_type,
            condition.target_name,
        )

        if self._runtime is not None:
            from ravi.kernel.core.identity import AgentId
            from ravi.kernel.messaging.message import Message, DataPayload

            combined_params = {**condition.target_params, "event": event}
            agent_id = AgentId(type=condition.target_type, key=condition.target_name)
            msg = Message(
                target=agent_id,
                payload=DataPayload(data=combined_params),
            )
            try:
                run_id = await self._runtime.submit(agent_id, msg)
                logger.info(
                    "Condition '%s' submitted run %s to native runtime for %s",
                    condition.name,
                    run_id,
                    agent_id,
                )
            except Exception as exc:
                logger.error(
                    "Condition '%s' failed to submit run for %s: %s",
                    condition.name,
                    agent_id,
                    exc,
                )
        else:
            logger.warning(
                "Condition '%s' triggered, but no Runtime is configured for dispatch.",
                condition.name,
            )
