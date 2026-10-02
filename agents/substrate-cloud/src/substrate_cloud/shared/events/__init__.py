"""Event envelope and Redis Streams backbone for async service communication."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from substrate.config import SubstrateConfig
    from substrate.integrations.events.redis_event_bus import EventBus


def get_event_bus(config: "SubstrateConfig | str") -> "EventBus":
    """Lazily import the concrete event-bus factory to avoid import cycles."""
    from substrate.serving.shared.events.factory import (
        get_event_bus as _factory_get_event_bus,
    )

    return _factory_get_event_bus(config)


__all__ = ["get_event_bus"]
