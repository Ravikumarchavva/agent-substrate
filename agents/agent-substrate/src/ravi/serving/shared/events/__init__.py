"""Event envelope and Redis Streams backbone for async service communication."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ravi.config import Settings
    from ravi.integrations.events.redis_event_bus import EventBus


def get_event_bus(config: "Settings | str") -> "EventBus":
    """Lazily import the concrete event-bus factory to avoid import cycles."""
    from ravi.serving.shared.events.factory import (
        get_event_bus as _factory_get_event_bus,
    )

    return _factory_get_event_bus(config)


__all__ = ["get_event_bus"]
