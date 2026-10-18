"""The typed event catalog: every domain event that crosses a process boundary, and nothing else.

    event                          produced by              consumed by
    conversation.thread_created    conversations            audit
    conversation.thread_deleted    conversations            audit
    workflow.run_started           runtime role             audit
    workflow.run_completed         runtime role             gateway (run status), audit
    workflow.run_failed            runtime role             gateway (run status), audit
    workflow.run_cancelled         runtime role             gateway (run status), audit
    job.cancel_requested           gateway                  runtime role (cancels the run)

What a client watches while a run is going (text, tool calls, approvals) is not an event: it is served from the run's
durable journal, so nothing is lost when a consumer is down. Every event names the tenant and user it is about, taken
from the verified caller of the request that began it.

    await bus.publish(RunCompleted(run_id=..., thread_id=..., tenant_id=..., user_id=..., steps=3).envelope())
    event = parse_event(envelope)          # -> RunCompleted
"""

from __future__ import annotations

from typing import ClassVar

from loamz.agents.kernel.events.envelope import EventEnvelope
from loamz.agents.kernel.types.content import KernelModel
from loamz.data.storage.errors import InvalidRequestError


class DomainEvent(KernelModel):
    """Base of every catalog event."""

    event_type: ClassVar[str]
    audited: ClassVar[bool] = True

    tenant_id: str
    user_id: str

    def envelope(self) -> EventEnvelope:
        return EventEnvelope(event_type=self.event_type, payload=self.model_dump(mode="json"))


class ThreadCreated(DomainEvent):
    event_type: ClassVar[str] = "conversation.thread_created"
    thread_id: str
    name: str = ""


class ThreadDeleted(DomainEvent):
    event_type: ClassVar[str] = "conversation.thread_deleted"
    thread_id: str


class _RunEvent(DomainEvent):
    run_id: str
    thread_id: str


class RunStarted(_RunEvent):
    event_type: ClassVar[str] = "workflow.run_started"


class RunCompleted(_RunEvent):
    event_type: ClassVar[str] = "workflow.run_completed"
    steps: int = 0


class RunFailed(_RunEvent):
    event_type: ClassVar[str] = "workflow.run_failed"
    error: str = ""


class RunCancelled(_RunEvent):
    event_type: ClassVar[str] = "workflow.run_cancelled"


class CancelRequested(_RunEvent):
    event_type: ClassVar[str] = "job.cancel_requested"
    audited: ClassVar[bool] = False
    runtime_run_id: str = ""


CATALOG: dict[str, type[DomainEvent]] = {
    cls.event_type: cls
    for cls in (
        ThreadCreated,
        ThreadDeleted,
        RunStarted,
        RunCompleted,
        RunFailed,
        RunCancelled,
        CancelRequested,
    )
}
AUDITED: tuple[str, ...] = tuple(name for name, cls in CATALOG.items() if cls.audited)


def parse_event(envelope: EventEnvelope) -> DomainEvent:
    """The typed event an envelope carries. An unknown type is refused, not guessed at."""
    cls = CATALOG.get(envelope.event_type)
    if cls is None:
        raise InvalidRequestError(f"no event {envelope.event_type!r} in the catalog")
    return cls.model_validate(envelope.payload)


__all__ = [
    "AUDITED",
    "CATALOG",
    "CancelRequested",
    "DomainEvent",
    "RunCancelled",
    "RunCompleted",
    "RunFailed",
    "RunStarted",
    "ThreadCreated",
    "ThreadDeleted",
    "parse_event",
]
