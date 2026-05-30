"""Shared event factories used by server and microservice lifespans."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ravi.adapters.events.redis_event_bus import EventBus

if TYPE_CHECKING:
    from ravi.config import Settings


def get_event_bus(config: "Settings | str") -> EventBus:
    """Build the concrete Redis-backed EventBus from settings or redis URL."""
    if isinstance(config, str):
        return EventBus(redis_url=config)
    return EventBus(redis_url=config.REDIS_URL)
