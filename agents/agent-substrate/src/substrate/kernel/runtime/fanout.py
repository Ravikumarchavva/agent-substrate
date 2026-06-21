"""FanoutStrategy — how an emitted message reaches all followers.

The strategy pattern keeps the delivery mechanism swappable without touching
the kernel contracts.  Stage 0 uses a simple push-to-all; Stage 3 adds the
celebrity-agent pull hybrid so a viral agent with 1M followers does not
trigger 1M synchronous inbox writes.

Fan-out is always initiated by a call to ``FanoutStrategy.publish`` — never
by the agent directly.  The agent calls ``ctx.emit(topic, msg)``; RunContext
(L1) looks up the FanoutStrategy from the runtime and delegates.

Stage 0 — push fan-out (simple, works for normal agents)
---------------------------------------------------------
For each follower of ``topic``:  ``Inbox.deliver(follower, msg)``
One synchronous inbox write per follower.  Fine at small scale.

Stage 3 — push/pull hybrid (celebrity agents)
----------------------------------------------
Agents with > N followers (configurable threshold) switch to pull:
- One write to a shared "latest post" store.
- Followers pull lazily when they next wake (for their own reasons).
- Avoids a synchronous write storm on a viral emit.
The threshold and pull store are implementation concerns, not kernel concerns.
"""

from __future__ import annotations

from typing import Protocol

from substrate.kernel.core.identity import TopicId
from substrate.kernel.messaging.message import Message
from substrate.kernel.runtime.follow_graph import FollowGraph
from substrate.kernel.runtime.inbox import Inbox


class FanoutStrategy(Protocol):
    """Contract for delivering an emitted message to all topic followers.

    Implementations choose whether to push synchronously (Stage 0),
    push asynchronously in batches (Stage 2), or use a push/pull hybrid
    for high-follower-count topics (Stage 3).
    """

    async def publish(
        self,
        topic: TopicId,
        msg: Message,
        *,
        graph: FollowGraph,
        inbox: Inbox,
    ) -> None:
        """Deliver ``msg`` to every agent that follows ``topic``.

        Implementations MUST be idempotent with respect to ``msg.id`` — if
        delivery to some followers fails mid-way and the call is retried, no
        follower should receive the same message twice.

        Implementations MUST NOT block indefinitely on slow followers — use
        fire-and-forget or bounded queues for fan-out to large subscriber sets.
        """
        ...


__all__ = ["FanoutStrategy"]
