"""InboxProtocol — durable per-agent mailbox.

The InboxProtocol is the delivery half of the social fabric.  Delivering a message to
a dormant agent is what *wakes* it — the InboxProtocol notifies the SchedulerProtocol which
enqueues a wakeup for the agent's run.

Robustness guarantees (all implementations must honour)
--------------------------------------------------------
1. **Exactly-once delivery tracking (dedup by Message.id).**
   ``deliver`` is idempotent: re-delivering the same ``Message.id`` is a
   no-op.  At-least-once transports (Redis Streams, NATS) re-deliver on
   subscriber restart; the InboxProtocol absorbs the duplicates so the agent never
   processes the same message twice.

2. **Per-sender FIFO ordering.**
   Messages from the same sender (keyed by ``Message.sender``) are drained
   in arrival order.  Messages from different senders may interleave.
   This prevents "post deleted" arriving before "post created" when both
   come from the same producer.

3. **Retry + dead-letter after N failures.**
   ``nack()`` increments the delivery attempt counter for a message.  When
   the counter reaches the implementation's ``max_retries`` ceiling, the
   message is moved to dead-letter storage and removed from the live inbox.
   The dead-letter queue is queryable via ``dead_letters()``.

Caller flow
-----------
Worker drains inbox → processes each message → on success ``ack(msg_id)`` →
on failure ``nack(msg_id, error=...)`` → SchedulerProtocol re-enqueues wakeup.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel

from substrate.kernel.abstractions.core.identity import Actor
from substrate.kernel.abstractions.messaging.message import Message


class DeadLetterReason(StrEnum):
    """Why a message ended up in the dead-letter queue."""

    MAX_RETRIES = "max_retries"
    EXPLICIT = "explicit"


class DeadLetterEntry(BaseModel):
    """A message that could not be delivered after exhausting retries.

    ``attempts`` is the total number of delivery attempts made before
    the message was dead-lettered.  ``last_error`` is the most recent
    error string from ``nack()``.
    """

    agent_id: Actor
    msg: Message
    reason: DeadLetterReason
    attempts: int
    last_error: str | None = None

    model_config = {"frozen": True, "arbitrary_types_allowed": True}


__all__ = ["DeadLetterReason", "DeadLetterEntry"]
