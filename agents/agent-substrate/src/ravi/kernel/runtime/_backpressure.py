"""Backpressure policies for bounded mailboxes and fan-out dispatch.

Every place the runtime accepts an envelope under capacity pressure has to
make a choice: block the producer, drop the new envelope, evict the oldest
envelope, or shed and signal. :class:`BackpressurePolicy` makes that choice
explicit and observable.

When a mailbox sheds, the dispatcher emits a :class:`BackpressureSignal` —
a structured event hyperscale operators can subscribe to (SRE dashboards,
auto-scalers, circuit breakers) instead of grepping for log warnings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from ravi.kernel.runtime._identity import AgentId, TopicId

__all__ = [
    "BackpressurePolicy",
    "BackpressureAction",
    "BackpressureSignal",
]


class BackpressurePolicy(Enum):
    """How a mailbox / dispatcher reacts when capacity is exhausted.

    - ``BLOCK``        — producer awaits free space; preserves all messages but
                         can deadlock or stall the fabric.
    - ``DROP_NEWEST``  — silently discard the incoming envelope.
    - ``DROP_OLDEST``  — evict the oldest queued envelope to make room.
    - ``SHED``         — raise ``MailboxFullError`` and emit a
                         ``BackpressureSignal`` so the fabric observes the loss.
    """

    BLOCK = "block"
    DROP_NEWEST = "drop_newest"
    DROP_OLDEST = "drop_oldest"
    SHED = "shed"


class BackpressureAction(Enum):
    """What actually happened when capacity was reached."""

    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    DROPPED_NEWEST = "dropped_newest"
    DROPPED_OLDEST = "dropped_oldest"
    SHED = "shed"


@dataclass(frozen=True, slots=True)
class BackpressureSignal:
    """Structured event emitted by mailboxes / dispatchers under load.

    Carries everything an operator needs to attribute the loss: which agent
    or topic was overwhelmed, what was sacrificed (``action``), what the
    queue depth was at the moment of the signal, and which envelope (by
    correlation id) was affected.
    """

    target: AgentId | TopicId
    policy: BackpressurePolicy
    action: BackpressureAction
    queue_depth: int
    capacity: int
    correlation_id: str
    sender: AgentId | None = None
    emitted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    reason: str = ""
