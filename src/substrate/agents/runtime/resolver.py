"""ActorResolver — activate actors on demand instead of holding every instance.

Registering one agent object per entity does not scale: a chat product with a
million conversations would pin a million agent objects in memory forever, and
lose all of them on restart. This is the virtual-actor pattern Orleans and Akka
Cluster Sharding use instead — register a *factory* per actor type (bounded:
you can enumerate them), then materialize instances on demand from their
address and drop them when idle. Memory tracks the active working set, not the
total entity count.

Eviction is safe here because an agent object holds no durable state: a
``ReActAgent`` loads conversation history from its ``HistoryProvider`` at the
start of every run and writes it back at the end, so the object itself carries
only configuration. Rebuilding it from the factory produces an identical actor.

The one case where that is not true is an in-memory ``HistoryProvider``, where
the "durable" store is a dict living inside the agent's own config — evicting
then rebuilding silently loses the conversation. ``allow_unsafe_eviction``
guards that: by default such actors are kept resident rather than evicted, so
dev behaviour doesn't quietly diverge from production.
"""

from __future__ import annotations

import inspect
import logging
import time
from collections import OrderedDict
from typing import Awaitable, Callable, Union

from substrate.kernel.core.identity import Actor
from substrate.kernel.runtime.agent import Agent

logger = logging.getLogger(__name__)

ActorFactory = Callable[[Actor], Union[Agent, Awaitable[Agent]]]
"""Builds an actor from its address. ``actor.type`` selected this factory;
``actor.key`` says which instance to build. May be sync or async — building
a real agent (e.g. acquiring its HITL bridge, wiring history) is usually a
coroutine, so ``resolve()`` awaits the result when the factory returns one."""


def _history_is_in_memory(agent: Agent) -> bool:
    """True when this agent's history lives in the object itself, making
    eviction lossy. Unknown providers are treated as durable — this only
    ever *adds* pinning, so a wrong guess costs memory, never a conversation.

    Imported lazily: ``agents/context`` pulls in the compaction pipeline,
    which this module has no reason to load just to be imported.
    """
    provider = getattr(agent, "history", None)
    if provider is None:
        return False
    try:
        from substrate.agents.storage import InMemoryHistoryProvider

        if isinstance(provider, InMemoryHistoryProvider):
            return True
    except ImportError:  # pragma: no cover - context layer always present
        pass
    # Fallback for stand-ins that mirror the provider without subclassing it.
    return "InMemoryHistoryProvider" in type(provider).__name__


class ActorResolver:
    """Resolve an address to a live actor, activating and evicting as needed.

    ``max_live`` caps resident actors (LRU beyond it); ``idle_ttl`` evicts
    actors untouched for that many seconds. Both are ceilings, not guarantees:
    an actor whose history is in-memory is never evicted unless
    ``allow_unsafe_eviction`` is set.
    """

    def __init__(
        self,
        *,
        max_live: int = 10_000,
        idle_ttl: float = 300.0,
        allow_unsafe_eviction: bool = False,
    ) -> None:
        self._factories: dict[str, ActorFactory] = {}
        self._live: OrderedDict[Actor, tuple[Agent, float]] = OrderedDict()
        self._pinned: set[Actor] = set()
        self._max_live = max_live
        self._idle_ttl = idle_ttl
        self._allow_unsafe_eviction = allow_unsafe_eviction

    # -- registration ---------------------------------------------------------

    def register_factory(self, actor_type: str, factory: ActorFactory) -> None:
        """Register how to build actors of ``actor_type``, for every instance."""
        self._factories[actor_type] = factory

    def register_instance(self, agent: Agent, *, pinned: bool = True) -> None:
        """Register one already-built actor.

        ``pinned=True`` (the default) is the escape hatch for singletons and
        for callers that build an actor eagerly and want it kept resident
        for the process lifetime — unchanged behavior for every existing
        caller.

        ``pinned=False`` is what turns an ordinary "build one instance per
        entity" call site into virtual-actor behavior *without changing how
        that instance is built*: the object is registered exactly the same
        way, but now participates in the same LRU/idle-TTL eviction a
        factory-activated actor gets. This is the intended migration path —
        a caller that already constructs a fresh, fully-current instance on
        every call (a per-request chat agent, say) doesn't need a factory at
        all to behave like a virtual actor: eviction is safe because the
        next call rebuilds it anyway, and not pinning it is what stops a
        million distinct entities from being held in memory forever.
        """
        self._live[agent.id] = (agent, time.monotonic())
        if pinned:
            self._pinned.add(agent.id)
        else:
            # Capacity eviction otherwise only ever ran from resolve()'s
            # factory-build branch — an unpinned instance registered
            # directly (the pinned=False migration path above) would never
            # be capacity-bounded, only idle-TTL-bounded. Enforcing it here
            # too makes max_live an actual ceiling regardless of how an
            # actor entered the registry.
            self._evict_over_capacity()

    # -- resolution -----------------------------------------------------------

    async def resolve(self, actor: Actor) -> Agent | None:
        """Return the live actor for this address, activating it if needed.

        ``None`` means neither a live instance nor a factory exists — the
        caller decides what that means (the Worker holds the lease so a
        late registration can still pick the run up).
        """
        self._evict_idle()
        live = self._live.get(actor)
        if live is not None:
            self._live.move_to_end(actor)
            self._live[actor] = (live[0], time.monotonic())
            return live[0]

        factory = self._factories.get(actor.type)
        if factory is None:
            return None

        agent = factory(actor)
        if inspect.isawaitable(agent):
            agent = await agent
        self._live[actor] = (agent, time.monotonic())
        if not self._allow_unsafe_eviction and _history_is_in_memory(agent):
            # Evicting would drop this conversation entirely — see module docstring.
            self._pinned.add(actor)
        self._evict_over_capacity()
        return agent

    # -- eviction -------------------------------------------------------------

    def _evict_idle(self) -> None:
        if self._idle_ttl <= 0:
            return
        cutoff = time.monotonic() - self._idle_ttl
        for actor in [a for a, (_, seen) in self._live.items() if seen < cutoff]:
            self._evict(actor)

    def _evict_over_capacity(self) -> None:
        while len(self._live) > self._max_live:
            for actor in list(self._live):  # oldest first
                if self._evict(actor):
                    break
            else:
                return  # everything resident is pinned; nothing to reclaim

    def _evict(self, actor: Actor) -> bool:
        if actor in self._pinned:
            return False
        self._live.pop(actor, None)
        logger.debug("Evicted idle actor %s", actor)
        return True

    # -- introspection --------------------------------------------------------

    @property
    def live_count(self) -> int:
        return len(self._live)

    def __contains__(self, actor: Actor) -> bool:
        return actor in self._live


__all__ = ["ActorResolver", "ActorFactory"]
