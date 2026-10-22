"""Pairs: what two of a user's agents said to each other.

Each pair is a record in the runtime's channel `pair/<id>`: the request, then the answer, for every time one asked the
other. Nobody in the channel is woken (it has no members); it is how the conversation is kept in order, paged and
followed live, like any other. The `directory_pairs` row lists it: whose pairs there are and the latest line, so an agent
with a hundred contacts is a page of rows, not a hundred journals.
"""

from __future__ import annotations

from collections.abc import Callable

from loamz.engine.agents.kernel.runtime.channel import ChannelStore, EntryKind
from loamz.engine.agents.kernel.runtime.persistence.store import RuntimeStore
from loamz.engine.agents.kernel.types.identity import Actor
from loamz.server.agents.bootstrap.container import Container
from loamz.server.agents.features.directory.pairs.store import Pair, PairsStore

# A pair's channel never pauses: what is said in it is a record of work, not a conversation that could run away.
PAIR_BREAKER = 1_000_000
PREVIEW = 140


def agent_actor(agent_id: str) -> Actor:
    """Who speaks in a pair: the agent itself (not one of its chats)."""
    return Actor("agent", agent_id)


def ordered(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _line(text: str) -> str:
    one = " ".join(text.split())
    return one if len(one) <= PREVIEW else one[:PREVIEW] + "…"


class ChannelsStore(RuntimeStore):
    """The runtime store on the container's database, its tables made on first use.

    TODO(integration): the conversations context's runtime owns the one store a process uses (so the live feed hears
    what workers post); `channels` then returns that one.
    """

    _opened = False

    async def _tx[T](self, fn: Callable[..., T]) -> T:
        if not self._opened:
            await self.start()
            self._opened = True
        return await super()._tx(fn)


def channels(container: Container) -> ChannelStore:
    """Provider: the channel store (group and pair conversations) shared by everything in this container."""
    return ChannelsStore(container.database)


class PairsService:
    def __init__(self, store: PairsStore, channel_store: ChannelStore) -> None:
        self._store = store
        self._channels = channel_store

    async def get_or_create(self, tenant_id: str, user_id: str, asker: str, target: str) -> Pair:
        """The pair for these two agents, made (and its channel opened) the first time they talk."""
        low, high = ordered(asker, target)
        pair = await self._store.get_or_create(tenant_id, user_id, low, high)
        await self._channels.channel_open(pair.channel, tenant=tenant_id, breaker=PAIR_BREAKER)
        return pair

    async def record(
        self,
        pair: Pair,
        *,
        speaker: str,
        text: str,
        thread_id: str,
        part: str,
        status: str,
        asked: bool = False,
    ) -> int | None:
        """One line in the pair, said by `speaker`: the request (`part="ask"`; `asked` counts it as one more time they
        have talked) or what came back (`"answer"`). Said once however many times it is recorded."""
        result = await self._channels.channel_append(
            pair.channel,
            sender=agent_actor(speaker),
            text=text,
            fresh=True,
            kind=EntryKind.MESSAGE,
            dedup_key=f"{thread_id}:{part}",
            data={"thread_id": thread_id, "status": status},
            caused_by=thread_id,
        )
        if result.seq is None:
            return None
        await self._store.touch(pair, text=_line(text), sender=speaker, asked=asked)
        return result.seq

    async def of_agent(
        self,
        tenant_id: str,
        user_id: str,
        agent_id: str,
        *,
        q: str = "",
        before: float | None = None,
        limit: int = 30,
    ) -> list[tuple[Pair, str, str | None]]:
        """`(pair, other agent's name, other's picture)`, newest first."""
        return await self._store.of_agent(
            tenant_id, user_id, agent_id, q=q, before=before, limit=limit
        )

    async def pair_for(
        self, tenant_id: str, user_id: str, agent_id: str, pair_id: str
    ) -> Pair | None:
        """A pair, if it is this user's and this agent is one of its two."""
        return await self._store.get_of(tenant_id, user_id, agent_id, pair_id)

    async def delete_pairs_of(self, tenant_id: str, agent_id: str) -> None:
        """After an agent is deleted: every pair it was in goes, with what was said in it."""
        pairs = await self._store.with_agent(tenant_id, agent_id)
        for pair in pairs:
            await self._channels.channel_delete(pair.channel)
        await self._store.delete(tenant_id, [p.id for p in pairs])

    async def erase(self, tenant_id: str, user_id: str | None = None) -> int:
        pairs = await self._store.of_owner(tenant_id, user_id)
        for pair in pairs:
            await self._channels.channel_delete(pair.channel)
        await self._store.delete(tenant_id, [p.id for p in pairs])
        return len(pairs)


def pairs(container: Container) -> PairsService:
    return PairsService(PairsStore(container.database), container.get(channels))


__all__ = ["PairsService", "agent_actor", "channels", "pairs"]
