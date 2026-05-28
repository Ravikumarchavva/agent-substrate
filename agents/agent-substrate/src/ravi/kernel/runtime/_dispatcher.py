"""Message dispatcher — routes envelopes to agents and topic subscribers.

The ``Dispatcher`` is the core routing table: it knows which ``AgentId``
owns which ``Mailbox`` and which ``TopicId`` has which subscribers.

Concurrency
~~~~~~~~~~~
Registration mutations (``register_agent``, ``subscribe_to_topic``,
``unsubscribe``) and dispatch reads (``dispatch``) may be invoked from
multiple threads — the agent-runtime event loop, lease-renewal heartbeat
threads, and (in a distributed runtime) cross-worker rebalance callbacks.
A single ``threading.RLock`` guards every read or write of the shared
routing tables so the dispatcher stays correct under Python 3.14
free-threaded execution.

Backpressure
~~~~~~~~~~~~
Topic fan-out uses :meth:`Mailbox.put_nowait`, which now returns a
:class:`BackpressureAction`. The dispatcher emits a
:class:`BackpressureSignal` to every registered observer when an envelope
is shed, dropped, or evicted — replacing the previous "log a warning and
continue" silent-loss path.

Partition affinity
~~~~~~~~~~~~~~~~~~
When ``envelope.locality.partition_key`` is set, topic fan-out routes the
envelope only to the agent instance whose ``AgentId.key`` matches the
partition key — preserving per-partition ordering. When the partition key
does not match any instance, the topic's default broadcast fallback applies.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Callable, Optional

from ravi.kernel.runtime._backpressure import (
    BackpressureAction,
    BackpressureSignal,
)
from ravi.kernel.runtime._contracts import Envelope, Subscription
from ravi.kernel.runtime._errors import AgentNotFoundError, MailboxFullError
from ravi.kernel.runtime._identity import AgentId, TopicId
from ravi.kernel.runtime._mailbox import Mailbox

logger = logging.getLogger(__name__)

# Re-export so existing ``from _dispatcher import AgentNotFoundError`` works.
__all__ = ["Dispatcher", "AgentNotFoundError"]


BackpressureObserver = Callable[[BackpressureSignal], None]


class Dispatcher:
    """Routes envelopes to agent mailboxes or topic subscribers.

    Thread-safe: all reads and writes of the routing tables are guarded by
    a single ``threading.RLock``. Dispatch is async but never holds the
    lock across an ``await``; the lock window is short and reentrant.
    """

    __slots__ = (
        "_mailboxes",
        "_topic_subscribers",
        "_type_agents",
        "_lock",
        "_backpressure_observers",
    )

    def __init__(self) -> None:
        self._mailboxes: dict[AgentId, Mailbox] = {}
        self._topic_subscribers: dict[TopicId, list[Subscription]] = {}
        self._type_agents: dict[str, set[AgentId]] = {}
        self._lock = threading.RLock()
        self._backpressure_observers: list[BackpressureObserver] = []

    # -- agent registration -------------------------------------------------

    def register_agent(self, agent_id: AgentId, mailbox: Mailbox) -> None:
        """Associate *agent_id* with *mailbox*."""
        with self._lock:
            self._mailboxes[agent_id] = mailbox
            self._type_agents.setdefault(agent_id.type, set()).add(agent_id)
        logger.debug("registered agent %s", agent_id)

    def unregister_agent(self, agent_id: AgentId) -> None:
        """Remove *agent_id* from the routing table."""
        with self._lock:
            self._mailboxes.pop(agent_id, None)
            type_set = self._type_agents.get(agent_id.type)
            if type_set is not None:
                type_set.discard(agent_id)
                if not type_set:
                    del self._type_agents[agent_id.type]
            # Only remove subscriptions when no agents of this type remain.
            type_has_agents = bool(self._type_agents.get(agent_id.type))
            for topic in list(self._topic_subscribers.keys()):
                self._topic_subscribers[topic] = [
                    s
                    for s in self._topic_subscribers[topic]
                    if not (s.agent_type == agent_id.type and not type_has_agents)
                ]
        logger.debug("unregistered agent %s", agent_id)

    def get_mailbox(self, agent_id: AgentId) -> Optional[Mailbox]:
        """Return the mailbox for *agent_id*, or ``None``."""
        with self._lock:
            return self._mailboxes.get(agent_id)

    # -- topic subscriptions ------------------------------------------------

    def subscribe_to_topic(self, topic: TopicId, agent_type: str) -> Subscription:
        """Subscribe *agent_type* to *topic*.

        Idempotent: if ``(topic, agent_type)`` is already subscribed, the
        existing ``Subscription`` is returned and no new record is created.
        """
        with self._lock:
            existing = self._topic_subscribers.get(topic, [])
            for sub in existing:
                if sub.agent_type == agent_type:
                    return sub
            sub = Subscription(
                id=uuid.uuid4().hex,
                topic=topic,
                agent_type=agent_type,
            )
            self._topic_subscribers.setdefault(topic, []).append(sub)
        logger.debug("subscribed %s to %s", agent_type, topic)
        return sub

    def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription by its id.

        Cleans up the topic entry entirely when its last subscriber is
        removed, so ``topics`` doesn't accumulate empty keys in
        long-running processes.
        """
        with self._lock:
            for topic in list(self._topic_subscribers.keys()):
                filtered = [
                    s
                    for s in self._topic_subscribers[topic]
                    if s.id != subscription_id
                ]
                if filtered:
                    self._topic_subscribers[topic] = filtered
                else:
                    del self._topic_subscribers[topic]

    # -- backpressure observation ------------------------------------------

    def add_backpressure_observer(self, observer: BackpressureObserver) -> None:
        """Register a callback invoked every time fan-out sheds load.

        The callback receives a :class:`BackpressureSignal` and runs
        synchronously inside the dispatch path — keep it cheap (metric
        increment, enqueue to a background bus). Do not perform I/O.
        """
        with self._lock:
            self._backpressure_observers.append(observer)

    def _emit_backpressure(self, signal: BackpressureSignal) -> None:
        # Snapshot observers under the lock, invoke outside it.
        with self._lock:
            observers = list(self._backpressure_observers)
        for obs in observers:
            try:
                obs(signal)
            except Exception:
                logger.exception("backpressure observer raised; ignoring")

    # -- dispatch -----------------------------------------------------------

    async def dispatch(self, envelope: Envelope) -> None:
        """Route *envelope* to the correct mailbox(es).

        - ``AgentId`` target → single mailbox delivery (blocking ``put``).
        - ``TopicId`` target → fan-out to subscribers, honoring partition key
          when set, with per-mailbox backpressure policy applied.

        Raises ``AgentNotFoundError`` for unknown direct targets.
        """
        target = envelope.target

        if isinstance(target, AgentId):
            mailbox = self.get_mailbox(target)
            if mailbox is None:
                raise AgentNotFoundError(f"no mailbox registered for {target}")
            await mailbox.put(envelope)
            return

        if isinstance(target, TopicId):
            self._fan_out(envelope, target)
            return

        if target is None:
            raise ValueError(
                "Envelope.target is None; bind a target before dispatch"
            )
        raise TypeError(f"unsupported target type: {type(target)}")

    def _fan_out(self, envelope: Envelope, topic: TopicId) -> None:
        """Fan an envelope out to topic subscribers respecting partitioning."""
        partition_key = envelope.locality.partition_key

        with self._lock:
            subscribers = list(self._topic_subscribers.get(topic, []))
            # Build a snapshot of (agent_id, mailbox) pairs to iterate
            # outside the lock so observer callbacks and policy logic don't
            # race against mid-dispatch (un)subscribes.
            recipients: list[tuple[AgentId, Mailbox]] = []
            for sub in subscribers:
                agent_ids = self._type_agents.get(sub.agent_type, set())
                # Partition affinity: when the envelope names a partition,
                # only the agent instance whose key matches receives it.
                if partition_key:
                    matched = [aid for aid in agent_ids if aid.key == partition_key]
                    if matched:
                        agent_ids = set(matched)
                    # If no instance owns this partition key, fall through to
                    # broadcast — the receiving agent_type may not yet have a
                    # sharded layout, and we'd rather over-deliver than lose.
                for aid in agent_ids:
                    mbox = self._mailboxes.get(aid)
                    if mbox is not None:
                        recipients.append((aid, mbox))

        for aid, mbox in recipients:
            self._try_put(envelope, aid, mbox)

    def _try_put(self, envelope: Envelope, aid: AgentId, mbox: Mailbox) -> None:
        try:
            action = mbox.put_nowait(envelope)
        except MailboxFullError:
            signal = BackpressureSignal(
                target=aid,
                policy=mbox.policy,
                action=BackpressureAction.SHED,
                queue_depth=mbox.size,
                capacity=mbox.capacity,
                correlation_id=envelope.correlation_id,
                sender=envelope.sender,
                reason="mailbox at capacity under SHED policy",
            )
            self._emit_backpressure(signal)
            logger.warning(
                "shed envelope %s for %s (mailbox full)",
                envelope.correlation_id,
                aid,
            )
            return
        except Exception:
            logger.exception(
                "fan-out to %s failed for envelope %s",
                aid,
                envelope.correlation_id,
            )
            return

        if action in (
            BackpressureAction.DROPPED_NEWEST,
            BackpressureAction.DROPPED_OLDEST,
        ):
            signal = BackpressureSignal(
                target=aid,
                policy=mbox.policy,
                action=action,
                queue_depth=mbox.size,
                capacity=mbox.capacity,
                correlation_id=envelope.correlation_id,
                sender=envelope.sender,
                reason=(
                    "dropped newest envelope"
                    if action is BackpressureAction.DROPPED_NEWEST
                    else "evicted oldest envelope"
                ),
            )
            self._emit_backpressure(signal)

    # -- introspection ------------------------------------------------------

    @property
    def registered_agents(self) -> list[AgentId]:
        with self._lock:
            return list(self._mailboxes.keys())

    @property
    def topics(self) -> list[TopicId]:
        with self._lock:
            return list(self._topic_subscribers.keys())
