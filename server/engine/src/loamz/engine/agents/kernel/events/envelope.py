"""The wire shape of a domain event: what crosses a process boundary on the event bus."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from loamz.engine.agents.kernel.types.ids import new_id


class EventEnvelope(BaseModel):
    """A domain event as the bus carries it.

    ``event_type`` is the dotted catalog name (``conversation.thread_created``); ``payload`` is the event's fields as
    JSON; ``event_id`` (UUIDv7) lets a consumer drop a redelivery; ``trace_context`` carries W3C trace propagation.
    """

    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    event_id: str = Field(default_factory=new_id)
    trace_context: dict[str, str] | None = None

    def stream_key(self) -> str:
        """The stream an adapter keeps this type in: ``events:<event_type>``."""
        return f"events:{self.event_type}"


__all__ = ["EventEnvelope"]
