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
from enum import Enum


class ActorRole(str, Enum):
    """What kind of thing an address points at.

    Deliberately closed and small. ``role`` gates no behaviour — every actor
    gets the same ``RunContext`` with the same spawn/ask/emit/follow surface,
    and pub/sub is keyed on the run, not the role. What it *does* do is keep
    ids from colliding across categories (``proxy``'s well-known ``"job"``
    constant vs. an agent someone named ``"job"``) and tell a human reading a
    log or trace what they're looking at.
    """

    AGENT = "agent"
    """A registered, runnable actor whose next step the model decides."""

    FLOW = "flow"
    """A registered, runnable actor whose next step fixed code decides.

    Runtime-identical to ``AGENT`` — same registration, same protocol, same
    primitives. Kept separate because the address is the only place that
    distinction survives into logs and traces.
    """

    PROXY = "proxy"
    """A message originating outside the actor system (HTTP, a scheduled job)."""

    USER = "user"
    """A human. Never runs; used only to scope memory and history."""

    INTERNAL = "internal"
    """Framework-internal bookkeeping — run subscriptions, tool-chain
    invocations, capability owner tags. Never constructed by agent authors."""


@dataclass(frozen=True, slots=True)
class Actor:
    """Stable routing address for one actor.

    ``id`` is deliberately meaningful and predictable, not random: several
    call sites independently construct the same address without a shared
    lookup (the ``PROXY`` ``"http"``/``"job"`` constants), an agent must
    resolve to the same address across a process restart, and ``USER``
    addresses carry the real external user id so memory stays attached to
    the right person.
    """

    role: ActorRole
    id: str

    def __str__(self) -> str:
        return f"{self.role.value}/{self.id}"

    @classmethod
    def generate(cls, role: ActorRole) -> Actor:
        """Create an Actor with a random id, for genuinely anonymous actors."""
        return cls(role=role, id=uuid.uuid4().hex)


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


__all__ = ["ActorRole", "Actor", "Topic"]
