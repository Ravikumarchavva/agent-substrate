"""Event envelope and Redis Streams backbone for async service communication."""

from ravi.shared.events.factory import get_event_bus

__all__ = ["get_event_bus"]
