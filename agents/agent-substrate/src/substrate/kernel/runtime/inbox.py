"""Inbox — durable per-agent mailbox.

The Inbox is the delivery half of the social fabric.  Delivering a message to
a dormant agent is what *wakes* it — the Inbox notifies the Scheduler which
enqueues a wakeup for the agent's run.

Robustness guarantees (all implementations must honour)
--------------------------------------------------------
1. **Exactly-once delivery tracking (dedup by Message.id).**
   ``deliver`` is idempotent: re-delivering the same ``Message.id`` is a
   no-op.  At-least-once transports (Redis Streams, NATS) re-deliver on
   subscriber restart; the Inbox absorbs the duplicates so the agent never
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
on failure ``nack(msg_id, error=...)`` → Scheduler re-enqueues wakeup.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol

from pydantic import BaseModel

from substrate.kernel.core.identity import AgentId
from substrate.kernel.messaging.message import Message


class DeadLetterReason(str, Enum):
    """Why a message ended up in the dead-letter queue."""

    MAX_RETRIES = "max_retries"
    EXPLICIT = "explicit"


class DeadLetterEntry(BaseModel):
    """A message that could not be delivered after exhausting retries.

    ``attempts`` is the total number of delivery attempts made before
    the message was dead-lettered.  ``last_error`` is the most recent
    error string from ``nack()``.
    """

    agent_id: AgentId
    msg: Message
    reason: DeadLetterReason
    attempts: int
    last_error: str | None = None

    model_config = {"frozen": True, "arbitrary_types_allowed": True}


class Inbox(Protocol):
    """Durable per-agent mailbox.

    Implementations: in-memory dict of deques (Stage 0), Postgres table with
    ``(agent_id, msg_id)`` PK and retry counter (Stage 1), Redis Streams
    consumer group (Stage 2+).
    """

    async def deliver(
        self, agent_id: AgentId, msg: Message, *, notify: bool = True
    ) -> bool:
        """Deliver ``msg`` to ``agent_id``'s inbox.

        Returns ``True`` when the message was appended.  Returns ``False``
        when ``msg.id`` was already in the inbox (idempotent re-delivery).

        When ``notify`` is ``True`` (the default), implementations MUST trigger
        the deliver-hook so a dormant agent gets a run spawned. Callers that
        enqueue their own run (e.g. ``Runtime.submit``) pass ``notify=False`` to
        suppress the hook and avoid spawning a duplicate run.
        """
        ...

    async def drain(self, agent_id: AgentId, *, max: int = 100) -> list[Message]:
        """Return up to ``max`` pending messages in per-sender FIFO order.

        Does not ack them — the caller must call ``ack`` or ``nack`` for
        each message after processing.  Messages that have been drained but
        not yet acked remain in the inbox and are re-drained on the next call.
        """
        ...

    async def ack(self, agent_id: AgentId, msg_id: str) -> None:
        """Mark ``msg_id`` as successfully processed and remove it from the inbox."""
        ...

    async def nack(
        self,
        agent_id: AgentId,
        msg_id: str,
        *,
        error: str = "",
    ) -> None:
        """Record a delivery failure for ``msg_id``.

        Increments the attempt counter.  When the counter reaches the
        implementation's ``max_retries``, the message is moved to the
        dead-letter queue and removed from the live inbox.
        """
        ...

    async def dead_letters(self, agent_id: AgentId) -> list[DeadLetterEntry]:
        """Return all dead-lettered messages for ``agent_id``."""
        ...

    async def pending_count(self, agent_id: AgentId) -> int:
        """Return the number of unacked messages in ``agent_id``'s inbox."""
        ...


__all__ = ["DeadLetterReason", "DeadLetterEntry", "Inbox"]
