"""FollowGraph — the durable social follow-graph between agents.

Named ``FollowGraph`` (not ``SubscriptionGraph``) to avoid collision with
``kernel/graph.py::GraphStore``, which is the RAG knowledge graph (entities,
relationships, Cypher) — a completely different concept.

What this is
------------
"Agent A follows agent B" means: whenever B emits on a topic, A's inbox
receives the message.  This is the Facebook/Twitter social model applied to
agents:

- An **information agent** (e.g. "trades-watcher") follows external sources
  and emits structured findings on its own topic.
- A **personal agent** follows several information agents it cares about.
- When the trades-watcher emits, fan-out delivers to every follower's Inbox,
  waking each personal agent with the finding.

Relationship to existing primitives
------------------------------------
``TopicId`` and ``Subscription`` (``kernel/message.py``) are reused as-is —
they are the identity and record types.  ``FollowGraph`` is the durable store
that keeps the graph alive across restarts and provides the fan-out query
(``followers_of``).  ``FanoutStrategy`` (``kernel/runtime/fanout.py``) uses
``FollowGraph`` to enumerate followers and deliver via ``Inbox``.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol

from agent_substrate.kernel.core.identity import AgentId, TopicId
from agent_substrate.kernel.messaging.message import Subscription


class FollowGraph(Protocol):
    """Durable, queryable follow-graph between agents and topics.

    Implementations: in-memory dict (Stage 0), Postgres adjacency table
    ``(follower_id, topic_type, topic_source)`` (Stage 1), distributed
    graph store (Stage 3).

    Semantic guarantees
    -------------------
    - ``follow`` is idempotent: following the same (follower, topic) pair
      twice returns a new ``Subscription`` but does not duplicate fan-out.
    - ``unfollow`` is safe to call on an already-removed subscription (no-op).
    - ``followers_of`` is consistent: it reflects all ``follow`` calls that
      completed before it was invoked.
    """

    async def follow(
        self,
        follower: AgentId,
        topic: TopicId,
    ) -> Subscription:
        """Subscribe ``follower`` to ``topic``.

        Returns a ``Subscription`` record that can be passed to ``unfollow``.
        The subscription is durable — survives process restarts.
        """
        ...

    async def unfollow(self, sub: Subscription) -> None:
        """Remove the subscription identified by ``sub``.

        Safe to call on an expired or already-removed subscription.
        """
        ...

    def followers_of(self, topic: TopicId) -> AsyncIterator[AgentId]:
        """Yield all agents currently subscribed to ``topic``.

        Returns an async iterator directly (not a coroutine), matching
        ``EventLog.read``/``tail`` — callers use ``async for`` without awaiting.

        Used by ``FanoutStrategy`` to enumerate delivery targets.
        Order is unspecified; duplicates will not appear.
        """
        ...

    def following(self, agent: AgentId) -> AsyncIterator[TopicId]:
        """Yield all topics that ``agent`` is currently subscribed to."""
        ...


__all__ = ["FollowGraph"]
