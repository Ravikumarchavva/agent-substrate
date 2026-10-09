"""The feed hub: committed channel changes, fanned out to the people watching them.

The engine tells an observer about every entry after it commits, wherever it was committed: here in the web process or in a worker.
Each process runs a hub holding the connections it serves. With Redis, every process publishes what it sees and every process's hub
hears it, so a reply written by a worker on another machine reaches a person connected to this one. Without Redis the observer hands
changes straight to the local hub (one process, which is all there then is).

A message is small and says only *where* to look (``{"t": "entry", "channel", "seq", "kind"}``); a watcher reads the entry itself from the
store. Nothing here is the record: a watcher that falls behind or reconnects reads the channel after the last ``seq`` it has, so a lost message costs
a catch-up, never a message.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from substrate.runtime import ChannelChange

logger = logging.getLogger(__name__)

REDIS_CHANNEL = "substrate:feed"
Message = dict[str, Any]


@dataclass(eq=False)
class Watcher:
    """One open connection: whose it is, which channels it follows, and what has been said to it that it has not yet taken."""

    user: str
    channels: set[str]
    queue: asyncio.Queue[Message] = field(
        default_factory=lambda: asyncio.Queue(maxsize=1000)
    )
    overflowed: bool = False
    """It could not keep up and messages were dropped: it must catch up from the channels themselves."""


class FeedHub:
    def __init__(self) -> None:
        self._by_channel: dict[str, set[Watcher]] = {}
        self._by_user: dict[str, set[Watcher]] = {}

    def open(self, user: str, channels: Iterable[str]) -> Watcher:
        watcher = Watcher(user=user, channels=set())
        self._by_user.setdefault(user, set()).add(watcher)
        self.set_channels(watcher, channels)
        return watcher

    def set_channels(self, watcher: Watcher, channels: Iterable[str]) -> None:
        wanted = set(channels)
        for channel in watcher.channels - wanted:
            self._drop(self._by_channel, channel, watcher)
        for channel in wanted - watcher.channels:
            self._by_channel.setdefault(channel, set()).add(watcher)
        watcher.channels = wanted

    def close(self, watcher: Watcher) -> None:
        self.set_channels(watcher, ())
        self._drop(self._by_user, watcher.user, watcher)

    @staticmethod
    def _drop(index: dict[str, set[Watcher]], key: str, watcher: Watcher) -> None:
        held = index.get(key)
        if held is None:
            return
        held.discard(watcher)
        if not held:
            del index[key]

    def watchers(self) -> int:
        return sum(len(w) for w in self._by_user.values())

    def dispatch(self, message: Message) -> None:
        """Hand a message to whoever follows what it is about: an entry to the watchers of its channel, ``chats`` to one person's connections."""
        if message.get("t") == "entry":
            targets = self._by_channel.get(str(message.get("channel")), ())
        elif message.get("t") == "chats":
            targets = self._by_user.get(str(message.get("user")), ())
        else:
            return
        for watcher in list(targets):
            try:
                watcher.queue.put_nowait(message)
            except asyncio.QueueFull:
                watcher.overflowed = True


class Relay:
    """Gets a message to every process's hub. With Redis that is publish and subscribe; without it, the local hub directly."""

    def __init__(self, hub: FeedHub, redis: Any | None = None) -> None:
        self._hub = hub
        self._redis = redis

    async def publish(self, message: Message) -> None:
        if self._redis is None:
            self._hub.dispatch(message)
            return
        try:
            await self._redis.publish(REDIS_CHANNEL, json.dumps(message))
        except Exception:  # noqa: BLE001 - Redis down must not make a committed message vanish for the people on this process
            logger.warning(
                "feed relay could not publish; delivering locally only", exc_info=True
            )
            self._hub.dispatch(message)

    async def observe(self, change: ChannelChange) -> None:
        """The engine's observer: every entry that commits, from any run on this process."""
        await self.publish(
            {
                "t": "entry",
                "channel": change.channel,
                "seq": change.seq,
                "kind": change.kind.value,
            }
        )

    async def run(self) -> None:
        """Hear what every process published, until cancelled. Reconnects with a pause if the connection drops."""
        if self._redis is None:
            return
        while True:
            try:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(REDIS_CHANNEL)
                try:
                    async for raw in pubsub.listen():
                        if raw.get("type") != "message":
                            continue
                        try:
                            self._hub.dispatch(json.loads(raw["data"]))
                        except (ValueError, TypeError):
                            logger.warning("feed relay dropped an unreadable message")
                finally:
                    await pubsub.aclose()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning(
                    "feed relay lost its connection; retrying", exc_info=True
                )
            await asyncio.sleep(1.0)


__all__ = ["FeedHub", "Relay", "Watcher"]
