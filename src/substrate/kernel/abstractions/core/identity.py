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
    """

    type: str
    key: str = ""

    def __post_init__(self) -> None:
        if not self.type:
            raise ValueError("Actor type cannot be empty")
        if "/" in self.type:
            raise ValueError(f"Actor type cannot contain '/': {self.type!r}")

    @property
    def is_singleton(self) -> bool:
        """True if this address targets a singleton actor with no instance key."""
        return not bool(self.key)

    def __str__(self) -> str:
        return f"{self.type}/{self.key}" if self.key else self.type

    @classmethod
    def from_str(cls, address: str) -> Actor:
        """Reconstruct an Actor address from its string representation.

        Mirrors ``__str__``: splits on the first '/' into (type, key).
        If no '/' is present, key defaults to ''.
        """
        if not address:
            raise ValueError("Actor address string cannot be empty")
        type_, sep, key = address.partition("/")
        return cls(type=type_, key=key if sep else "")

    @classmethod
    def generate(cls, type: str) -> Actor:
        """Create an Actor with a random key, for genuinely anonymous actors."""
        return cls(type=type, key=uuid.uuid4().hex)

    @classmethod
    def system(cls, key: str = "bootstrap") -> Actor:
        """Standard address for system-originated operations."""
        return cls(type="system", key=key)

    @classmethod
    def user(cls, key: str = "default") -> Actor:
        """Standard address for human user ingress."""
        return cls(type="user", key=key)


@dataclass(frozen=True, slots=True)
class Topic:
    """Routing address for a pub/sub channel.

    One flat name, matching what real pub/sub primitives at this tier
    actually do — a Postgres ``LISTEN``/``NOTIFY`` channel and a Redis
    ``PUBLISH`` channel are both bare names with no metadata and no
    registration step.
    """

    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Topic name cannot be empty")

    def __str__(self) -> str:
        return self.name

    @classmethod
    def progress(cls, run_id: str) -> Topic:
        """Standard channel for execution progress events of a specific run."""
        return cls(f"agent.progress/{run_id}")

    @classmethod
    def stream(cls, run_id: str) -> Topic:
        """Standard channel for token streaming of a specific run."""
        return cls(f"agent.stream/{run_id}")


__all__ = ["Actor", "Topic"]