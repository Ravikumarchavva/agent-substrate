"""One person's feed: what is said in the conversations they are in, as it is said.

``feed_events`` is the stream behind ``POST /feed``. It first catches the client up from where it says it is (``since``, per conversation), then
follows the hub: each entry that commits in a conversation they are in is read from the store and sent, with typing shown as it changes.
Everything sent can be had again by reading the conversation after the last ``seq`` the client holds, so a dropped connection, a dropped
message or a slow client loses nothing: a ``resync`` tells it to ask again.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from substrate.runtime import ChannelEntry
from substrate.types import Actor
from substrate_cloud.realtime.hub import FeedHub

logger = logging.getLogger(__name__)

PAGE = 500


@dataclass(frozen=True)
class Chat:
    """A conversation the person is in, as the feed needs to know it."""

    group_id: str
    names: dict[str, str]
    """Everyone in it by the address entries carry, to the name they are known by."""
    agents: tuple[Actor, ...] = field(default_factory=tuple)
    """The actors whose work shows as typing."""


async def feed_events(
    *,
    me: Actor,
    since: dict[str, int],
    store: Any,
    hub: FeedHub,
    load: Callable[[], Awaitable[dict[str, Chat]]],
    render: Callable[[ChannelEntry, Chat], dict[str, Any]],
    read_position: Callable[[str], Awaitable[int]],
    gone: Callable[[], Awaitable[bool]],
    tick: float = 1.5,
    ping: float = 15.0,
) -> AsyncIterator[dict[str, Any] | None]:
    """Yield what to send: an event, or ``None`` for a keepalive. ``since`` maps a conversation's id to the last ``seq`` the client has."""
    chats = await load()
    watcher = hub.open(me.key, chats.keys())
    loop = asyncio.get_running_loop()
    typing: dict[str, list[str]] = {}
    read_by_all: dict[str, int] = {}
    last_sent = loop.time()
    last_presence = 0.0

    async def read_event(channel: str, chat: Chat) -> list[dict[str, Any]]:
        """How far the agents have read, when that is not what the client last heard (the ticks under your messages)."""
        position = await read_position(channel)
        if read_by_all.get(chat.group_id) == position:
            return []
        read_by_all[chat.group_id] = position
        return [{"type": "read", "chat": chat.group_id, "by_all": position}]

    async def deliver(
        channel: str, chat: Chat, entries: list[ChannelEntry]
    ) -> list[dict[str, Any]]:
        out = [
            {"type": "entry", "chat": chat.group_id, "entry": render(e, chat)}
            for e in entries
        ]
        if entries:
            await store.channel_mark_delivered(channel, me, entries[-1].seq)
            out += await read_event(channel, chat)
        return out

    try:
        yield {"type": "ready", "chats": [c.group_id for c in chats.values()]}
        for channel, chat in chats.items():
            after = since.get(chat.group_id)
            while after is not None:
                entries = await store.channel_read(channel, after=after, limit=PAGE)
                for event in await deliver(channel, chat, entries):
                    yield event
                if len(entries) < PAGE:
                    break
                after = entries[-1].seq
            for event in await read_event(channel, chat):
                yield event
        while True:
            if await gone():
                return
            try:
                message = await asyncio.wait_for(watcher.queue.get(), timeout=tick)
            except asyncio.TimeoutError:
                message = None
            if watcher.overflowed:
                watcher.overflowed = False
                while not watcher.queue.empty():
                    watcher.queue.get_nowait()
                last_sent = loop.time()
                yield {"type": "resync"}
                continue
            if message is not None:
                last_sent = loop.time()
                if message["t"] == "chats":
                    chats = await load()
                    hub.set_channels(watcher, chats.keys())
                    yield {
                        "type": "chats",
                        "chats": [c.group_id for c in chats.values()],
                    }
                    continue
                channel = str(message["channel"])
                chat = chats.get(channel)
                if chat is not None:
                    found = await store.channel_read(
                        channel, after=int(message["seq"]) - 1, limit=1
                    )
                    if found and str(found[0].sender) not in chat.names:
                        # Someone new spoke (an agent was added): learn who they are before showing it.
                        chats = await load()
                        hub.set_channels(watcher, chats.keys())
                        chat = chats.get(channel, chat)
                    for event in await deliver(channel, chat, found):
                        yield event
            now = loop.time()
            if now - last_presence >= tick:
                last_presence = now
                everyone = [a for chat in chats.values() for a in chat.agents]
                busy = set(await store.working(everyone)) if everyone else set()
                for chat in chats.values():
                    names = [
                        chat.names[str(a)]
                        for a in chat.agents
                        if a in busy and str(a) in chat.names
                    ]
                    if names != typing.get(chat.group_id, []):
                        typing[chat.group_id] = names
                        last_sent = now
                        yield {"type": "working", "chat": chat.group_id, "names": names}
                        # An agent that has finished has read what it was woken for: the ticks may have moved.
                        for event in await read_event(
                            next(c for c, v in chats.items() if v is chat), chat
                        ):
                            yield event
            if loop.time() - last_sent >= ping:
                last_sent = loop.time()
                yield None
    finally:
        hub.close(watcher)


__all__ = ["Chat", "feed_events"]
