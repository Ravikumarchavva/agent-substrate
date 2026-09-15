"""ActorResolver — on-demand activation and eviction.

Real resolver, real stub agents satisfying the kernel Agent Protocol, no
mocks. The scaling claim these pin: a factory registered once serves any
number of instances, and idle instances are reclaimed instead of pinned for
the process lifetime.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from substrate.agents.runtime.resolver import ActorResolver
from substrate.kernel.core.identity import Actor
from substrate.kernel.messaging.message import Message


@dataclass
class StubAgent:
    """Minimal Agent — records how many times its factory built it."""

    actor: Actor
    history: object | None = None
    seen: list = field(default_factory=list)

    @property
    def id(self) -> Actor:
        return self.actor

    async def run(self, ctx, inbox: list[Message]) -> None:
        self.seen.extend(inbox)


class _InMemoryHistoryProvider:
    """Name-matched stand-in — the resolver treats this type as lossy to evict."""


def test_unknown_type_resolves_to_none() -> None:
    """No instance, no factory — the Worker needs None so it can hold the lease."""
    r = ActorResolver()
    assert r.resolve(Actor(type="conversation", key="s1")) is None


def test_factory_activates_on_demand() -> None:
    r = ActorResolver()
    r.register_factory("conversation", lambda a: StubAgent(a))

    agent = r.resolve(Actor(type="conversation", key="s1"))

    assert agent is not None
    assert agent.id == Actor(type="conversation", key="s1")


def test_one_factory_serves_many_instances() -> None:
    """The whole point: registering once covers unbounded entities."""
    built: list[Actor] = []

    def factory(a: Actor) -> StubAgent:
        built.append(a)
        return StubAgent(a)

    r = ActorResolver()
    r.register_factory("conversation", factory)

    for i in range(1_000):
        r.resolve(Actor(type="conversation", key=f"s{i}"))

    assert len(built) == 1_000
    assert r.live_count == 1_000


def test_repeat_resolve_reuses_the_same_instance() -> None:
    builds = 0

    def factory(a: Actor) -> StubAgent:
        nonlocal builds
        builds += 1
        return StubAgent(a)

    r = ActorResolver()
    r.register_factory("conversation", factory)
    addr = Actor(type="conversation", key="s1")

    first = r.resolve(addr)
    second = r.resolve(addr)

    assert first is second
    assert builds == 1


def test_lru_eviction_bounds_memory() -> None:
    r = ActorResolver(max_live=10, idle_ttl=0)
    r.register_factory("conversation", lambda a: StubAgent(a))

    for i in range(100):
        r.resolve(Actor(type="conversation", key=f"s{i}"))

    assert r.live_count <= 10, "resolver must not grow without bound"
    # The most recent survives; the oldest was reclaimed.
    assert Actor(type="conversation", key="s99") in r
    assert Actor(type="conversation", key="s0") not in r


def test_evicted_actor_is_rebuilt_on_next_resolve() -> None:
    """Eviction must be transparent — a reclaimed actor comes back."""
    builds: list[str] = []
    r = ActorResolver(max_live=2, idle_ttl=0)
    r.register_factory(
        "conversation", lambda a: (builds.append(a.key), StubAgent(a))[1]
    )

    first = Actor(type="conversation", key="s1")
    r.resolve(first)
    for i in range(5):  # push s1 out
        r.resolve(Actor(type="conversation", key=f"filler{i}"))
    assert first not in r

    again = r.resolve(first)

    assert again is not None
    assert again.id == first
    assert builds.count("s1") == 2, "should have been rebuilt, not resurrected"


def test_registered_instance_is_never_evicted() -> None:
    """register() pins — a singleton must not vanish under load."""
    r = ActorResolver(max_live=2, idle_ttl=0)
    pinned = StubAgent(Actor(type="http_proxy"))
    r.register_instance(pinned)
    r.register_factory("conversation", lambda a: StubAgent(a))

    for i in range(50):
        r.resolve(Actor(type="conversation", key=f"s{i}"))

    assert r.resolve(Actor(type="http_proxy")) is pinned


def test_in_memory_history_actor_is_not_evicted() -> None:
    """The dev/prod divergence guard: evicting an actor whose history lives
    in the object itself would silently drop the conversation."""
    r = ActorResolver(max_live=2, idle_ttl=0)
    r.register_factory(
        "conversation",
        lambda a: StubAgent(a, history=_InMemoryHistoryProvider()),
    )

    lossy = Actor(type="conversation", key="s1")
    agent = r.resolve(lossy)
    for i in range(20):
        r.resolve(Actor(type="conversation", key=f"filler{i}"))

    assert lossy in r, "in-memory-history actor must be pinned, not evicted"
    assert r.resolve(lossy) is agent


def test_unsafe_eviction_opt_in_allows_reclaiming_in_memory_history() -> None:
    r = ActorResolver(max_live=2, idle_ttl=0, allow_unsafe_eviction=True)
    r.register_factory(
        "conversation",
        lambda a: StubAgent(a, history=_InMemoryHistoryProvider()),
    )

    lossy = Actor(type="conversation", key="s1")
    r.resolve(lossy)
    for i in range(20):
        r.resolve(Actor(type="conversation", key=f"filler{i}"))

    assert lossy not in r


def test_factories_are_selected_by_type() -> None:
    """``type`` is the activation key — this is what it exists for."""
    r = ActorResolver()
    r.register_factory("conversation", lambda a: StubAgent(a, history="conv"))
    r.register_factory("data_analyst", lambda a: StubAgent(a, history="analyst"))

    conv = r.resolve(Actor(type="conversation", key="s1"))
    analyst = r.resolve(Actor(type="data_analyst", key="s1"))

    assert conv is not None and analyst is not None
    assert conv.history == "conv"
    assert analyst.history == "analyst"
    assert conv is not analyst, "same key, different type — distinct actors"
