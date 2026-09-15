"""Actor and topic routing identities.

Both are pure addresses — the whole representation of "what is this", with no
backing object anywhere. There is no ``Actor`` class holding state and no
``Topic`` class holding subscribers; an actor's state lives in its event log,
and a topic's subscribers live in the ``FollowGraph``. Keeping these as two
distinct types (rather than plain strings) is what lets ``Message.target``
dispatch point-to-point sends from broadcasts via ``isinstance``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Actor:
    """Stable routing address for one actor: which definition, which instance.

    ``type`` says *how to build this* — it selects the factory the runtime
    calls to activate the actor. It is bounded: the set of registered
    factories, small enough to enumerate (``"conversation"``, ``"video"``,
    ``"http_proxy"``).

    ``key`` says *which instance* — the entity this address points at. It is
    unbounded: one per conversation, per video, per user. ``""`` means a
    singleton, where the type has exactly one instance.

    Both parts are deliberately meaningful and predictable rather than
    random: several call sites independently construct the same address
    without a shared lookup (the ``http_proxy`` constant), an actor must
    resolve to the same address across a process restart, and user-scoped
    addresses carry the real external id so memory stays attached to the
    right person.
    """

    type: str
    key: str = ""

    def __str__(self) -> str:
        return f"{self.type}/{self.key}" if self.key else self.type

    @classmethod
    def generate(cls, type: str) -> Actor:
        """Create an Actor with a random key, for genuinely anonymous actors."""
        return cls(type=type, key=uuid.uuid4().hex)


@dataclass(frozen=True, slots=True)
class Topic:
    """Routing address for a pub/sub channel.

    One flat name, matching what real pub/sub primitives at this tier
    actually do — a Postgres ``LISTEN``/``NOTIFY`` channel and a Redis
    ``PUBLISH`` channel are both bare names with no metadata and no
    registration step. Scoping to an instance is done by putting it in the
    name (``"agent.progress/<run_id>"``), not by a second field.

    Standard conventions:
        agent.progress/<run_id>   — all progress events for one execution run
        agent.stream/<run_id>     — token stream for a specific run
    """

    name: str

    def __str__(self) -> str:
        return self.name


__all__ = ["Actor", "Topic"]
