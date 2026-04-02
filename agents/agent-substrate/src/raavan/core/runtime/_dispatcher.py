"""Message dispatcher — routes envelopes to agents and topic subscribers.

The ``Dispatcher`` is the core routing table: it knows which ``AgentId``
owns which ``Mailbox`` and which ``TopicId`` has which subscribers.

Uses a reverse index (``_type_agents``) for O(1) fan-out by agent type.
"""

from __future__ import annotations

import logging
import uuid
from typing import Dict, List, Optional, Set

from raavan.core.runtime._protocol import AgentId, TopicId
from raavan.core.runtime._types import Envelope, Subscription
from raavan.core.runtime._mailbox import Mailbox

logger = logging.getLogger("raavan.core.runtime.dispatcher")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AgentNotFoundError(Exception):
    """Raised when dispatching to an ``AgentId`` that has no registered mailbox."""


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class Dispatcher:
    """Routes envelopes to agent mailboxes or topic subscribers.

    Thread-safety is *not* required — all callers run on the same
    asyncio event loop.
    """

    __slots__ = ("_mailboxes", "_topic_subscribers", "_type_agents")

    def __init__(self) -> None:
        self._mailboxes: Dict[AgentId, Mailbox] = {}
        self._topic_subscribers: Dict[TopicId, List[Subscription]] = {}
        # M1 fix: reverse index for O(1) fan-out by agent type
        self._type_agents: Dict[str, Set[AgentId]] = {}

    # -- agent registration -------------------------------------------------

    def register_agent(self, agent_id: AgentId, mailbox: Mailbox) -> None:
        """Associate *agent_id* with *mailbox*."""
        self._mailboxes[agent_id] = mailbox
        self._type_agents.setdefault(agent_id.type, set()).add(agent_id)
        logger.debug("registered agent %s", agent_id)

    def unregister_agent(self, agent_id: AgentId) -> None:
        """Remove *agent_id* from the routing table.

        H1 fix: only removes this specific AgentId, not all agents of
        the same type.
        """
        self._mailboxes.pop(agent_id, None)
        # Remove from reverse index
        type_set = self._type_agents.get(agent_id.type)
        if type_set is not None:
            type_set.discard(agent_id)
            if not type_set:
                del self._type_agents[agent_id.type]
        # H1 fix: only remove subscriptions for this specific agent_id
        for topic, subs in self._topic_subscribers.items():
            self._topic_subscribers[topic] = [
                s
                for s in subs
                if not (
                    s.agent_type == agent_id.type
                    and len(self._type_agents.get(agent_id.type, set())) == 0
                )
            ]
        logger.debug("unregistered agent %s", agent_id)

    def get_mailbox(self, agent_id: AgentId) -> Optional[Mailbox]:
        """Return the mailbox for *agent_id*, or ``None``."""
        return self._mailboxes.get(agent_id)

    # -- topic subscriptions ------------------------------------------------

    def subscribe_to_topic(self, topic: TopicId, agent_type: str) -> Subscription:
        """Subscribe *agent_type* to *topic*.

        Returns the ``Subscription`` record so callers can unsubscribe later.
        """
        sub = Subscription(
            id=uuid.uuid4().hex,
            topic=topic,
            agent_type=agent_type,
        )
        self._topic_subscribers.setdefault(topic, []).append(sub)
        logger.debug("subscribed %s to %s", agent_type, topic)
        return sub

    def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription by its id."""
        for topic, subs in self._topic_subscribers.items():
            self._topic_subscribers[topic] = [
                s for s in subs if s.id != subscription_id
            ]

    # -- dispatch -----------------------------------------------------------

    async def dispatch(self, envelope: Envelope) -> None:
        """Route *envelope* to the correct mailbox(es).

        - ``AgentId`` target → single mailbox delivery.
        - ``TopicId`` target → fan-out to all subscribers (best-effort).

        Raises ``AgentNotFoundError`` for unknown direct targets.
        """
        target = envelope.target

        if isinstance(target, AgentId):
            mailbox = self._mailboxes.get(target)
            if mailbox is None:
                raise AgentNotFoundError(f"no mailbox registered for {target}")
            await mailbox.put(envelope)

        elif isinstance(target, TopicId):
            subscribers = self._topic_subscribers.get(target, [])
            for sub in subscribers:
                # M1 fix: O(1) lookup via reverse index instead of scanning all agents
                agent_ids = self._type_agents.get(sub.agent_type, set())
                # H2 fix: snapshot to avoid mutation during iteration
                for aid in list(agent_ids):
                    mbox = self._mailboxes.get(aid)
                    if mbox is None:
                        continue
                    # C6 fix: isolate each put — one failure doesn't stop others
                    try:
                        mbox.put_nowait(envelope)
                    except Exception:
                        logger.warning(
                            "fan-out to %s failed for topic %s, skipping",
                            aid,
                            target,
                        )

        else:
            raise TypeError(f"unsupported target type: {type(target)}")

    # -- introspection ------------------------------------------------------

    @property
    def registered_agents(self) -> list[AgentId]:
        return list(self._mailboxes.keys())

    @property
    def topics(self) -> list[TopicId]:
        return list(self._topic_subscribers.keys())
