"""Shared event factories used by server and microservice lifespans."""

from __future__ import annotations

from typing import TYPE_CHECKING

from substrate.integrations.events.redis_event_bus import EventBus

if TYPE_CHECKING:
    from substrate.config import SubstrateConfig


def get_event_bus(config: "SubstrateConfig | str") -> EventBus:
    """Build the concrete Redis-backed EventBus from settings or redis URL."""
    if isinstance(config, str):
        return EventBus(redis_url=config)
    return EventBus(redis_url=config.REDIS_URL)
