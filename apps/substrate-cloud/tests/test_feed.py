"""The feed: what is said in your conversations reaches you as it commits, and nothing is lost when the stream is not there to carry it."""

from __future__ import annotations

import asyncio
from typing import Any

from substrate.runtime import Member, ParticipantKind
from substrate.testing.runtime import ephemeral_runtime
from substrate.types import Actor
from substrate_cloud.realtime.feed import Chat, feed_events
from substrate_cloud.realtime.hub import FeedHub, Relay

ME = Actor("user", "ravi")
SCOUT = Actor("member", "scout@g")
CH = "group/g"


def chat(names: dict[str, str] | None = None) -> Chat:
    return Chat(
        group_id="g",
        names=names or {str(ME): "You", str(SCOUT): "Scout"},
        agents=(SCOUT,),
    )


async def open_channel(store: Any) -> None:
    await store.channel_open(
        CH, members=[Member(agent=ME, kind=ParticipantKind.HUMAN), Member(agent=SCOUT)]
    )


def render(entry: Any, chat: Chat) -> dict[str, Any]:
    return {
        "seq": entry.seq,
        "text": entry.text,
        "sender": chat.names.get(str(entry.sender), "?"),
        "kind": entry.kind.value,
    }


async def never_gone() -> bool:
    return False


_END = object()


class Pump:
    """Runs a feed in the background and hands over what it sent. (Cancelling a wait on the generator itself would end it, as a closed connection does.)"""

    def __init__(self, events: Any) -> None:
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._task = asyncio.create_task(self._run(events))
        self.ended = False

    async def _run(self, events: Any) -> None:
        try:
            async for event in events:
                await self._queue.put(event)
        finally:
            await self._queue.put(_END)

    async def take(self, count: int, *, timeout: float = 5.0) -> list[Any]:
        """The next ``count`` events (keepalives included), or whatever arrived within ``timeout``."""
        got: list[Any] = []
        end = asyncio.get_running_loop().time() + timeout
        while len(got) < count and not self.ended:
            left = end - asyncio.get_running_loop().time()
            if left <= 0:
                break
            try:
                event = await asyncio.wait_for(self._queue.get(), left)
            except asyncio.TimeoutError:
                break
            if event is _END:
                self.ended = True
                break
            got.append(event)
        return got

    async def entries(self, count: int, *, timeout: float = 5.0) -> list[Any]:
        """The next ``count`` entries, passing over anything else the feed says in between (typing, say)."""
        got: list[Any] = []
        end = asyncio.get_running_loop().time() + timeout
        while len(got) < count:
            left = end - asyncio.get_running_loop().time()
            if left <= 0:
                break
            got += [
                e
                for e in await self.take(1, timeout=left)
                if isinstance(e, dict) and e["type"] == "entry"
            ]
            if self.ended:
                break
        return got

    async def close(self) -> None:
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)


def start(**kwargs: Any) -> Pump:
    store = kwargs["store"]

    async def read_position(channel: str) -> int:
        return min(
            (
                m.cursor
                for m in await store.channel_members(channel)
                if m.kind is ParticipantKind.AGENT
            ),
            default=-1,
        )

    return Pump(
        feed_events(
            gone=never_gone,
            render=render,
            read_position=read_position,
            tick=0.05,
            **kwargs,
        )
    )


# -- the hub ----------------------------------------------------------------------------------------------------------------------------


def test_an_entry_reaches_only_those_following_its_channel_and_chats_only_that_person():
    hub = FeedHub()
    a, b, c = hub.open("a", ["g1"]), hub.open("b", ["g1", "g2"]), hub.open("c", ["g2"])
    hub.dispatch({"t": "entry", "channel": "g1", "seq": 0})
    assert [w.queue.qsize() for w in (a, b, c)] == [1, 1, 0]
    hub.dispatch({"t": "chats", "user": "c"})
    assert [w.queue.qsize() for w in (a, b, c)] == [1, 1, 1]
    hub.set_channels(b, ["g2"])
    hub.dispatch({"t": "entry", "channel": "g1", "seq": 1})
    assert [w.queue.qsize() for w in (a, b, c)] == [2, 1, 1]
    hub.close(a)
    hub.dispatch({"t": "entry", "channel": "g1", "seq": 2})
    assert a.queue.qsize() == 2 and hub.watchers() == 2


def test_a_watcher_that_cannot_keep_up_is_told_to_catch_up_rather_than_holding_the_rest_back():
    hub = FeedHub()
    slow = hub.open("slow", ["g"])
    slow.queue = asyncio.Queue(maxsize=2)
    fast = hub.open("fast", ["g"])
    for seq in range(5):
        hub.dispatch({"t": "entry", "channel": "g", "seq": seq})
    assert slow.overflowed and slow.queue.qsize() == 2
    assert not fast.overflowed and fast.queue.qsize() == 5


def test_an_unknown_message_is_ignored():
    hub = FeedHub()
    watcher = hub.open("a", ["g"])
    hub.dispatch({"t": "mystery"})
    hub.dispatch({})
    assert watcher.queue.empty()


async def test_the_relay_delivers_locally_without_redis_and_over_it_when_there_is():
    hub = FeedHub()
    watcher = hub.open("a", ["g"])
    await Relay(hub).publish({"t": "entry", "channel": "g", "seq": 0})
    assert watcher.queue.qsize() == 1

    class Bus:
        def __init__(self) -> None:
            self.sent: list[str] = []

        async def publish(self, channel: str, data: str) -> None:
            self.sent.append(data)

    bus = Bus()
    await Relay(hub, bus).publish({"t": "entry", "channel": "g", "seq": 1})
    assert (
        len(bus.sent) == 1 and watcher.queue.qsize() == 1
    )  # it went out to be heard back, not straight in

    class Down:
        async def publish(self, channel: str, data: str) -> None:
            raise ConnectionError("redis is down")

    await Relay(hub, Down()).publish({"t": "entry", "channel": "g", "seq": 2})
    assert (
        watcher.queue.qsize() == 2
    )  # a committed message is not lost to the people on this process


# -- the stream -------------------------------------------------------------------------------------------------------------------------


async def test_the_feed_catches_you_up_from_where_you_are_then_follows_what_is_said():
    async with ephemeral_runtime() as rt:
        store, hub = rt.store, FeedHub()
        await open_channel(store)
        store.channel_observe(Relay(hub).observe)
        for i in range(3):
            await store.channel_append(CH, sender=SCOUT, text=f"old {i}", fresh=True)

        events = start(
            me=ME, since={"g": 0}, store=store, hub=hub, load=lambda: _load(chat())
        )
        first = await events.take(4)
        assert [e["type"] for e in first] == ["ready", "entry", "entry", "read"]
        assert [e["entry"]["text"] for e in first[1:3]] == [
            "old 1",
            "old 2",
        ]  # what came after seq 0, not before

        async def speak() -> None:
            await asyncio.sleep(0.1)
            await store.channel_append(CH, sender=SCOUT, text="live", fresh=True)

        asyncio.create_task(speak())
        live = await events.entries(1)
        assert live and live[0]["entry"]["text"] == "live" and live[0]["chat"] == "g"
        # What it sent has reached you: that is the second tick for your device.
        assert {m.agent: m.delivered for m in await store.channel_members(CH)}[ME] == 3
        await events.close()
        assert hub.watchers() == 0


async def _zero() -> int:
    return -1


async def _load(chats: Chat) -> dict[str, Chat]:
    return {CH: chats}


async def test_edits_and_reactions_arrive_as_entries_of_their_own():
    async with ephemeral_runtime() as rt:
        store, hub = rt.store, FeedHub()
        await open_channel(store)
        store.channel_observe(Relay(hub).observe)
        said = await store.channel_append(CH, sender=ME, text="lunch at 1")
        events = start(
            me=ME,
            since={"g": said.seq},
            store=store,
            hub=hub,
            load=lambda: _load(chat()),
        )
        await events.take(2)  # ready, and how far the agents have read
        await store.channel_edit(CH, said.seq, ME, "lunch at 2")
        await store.channel_react(CH, said.seq, SCOUT, "👍")
        got = await events.entries(2)
        assert [(e["entry"]["kind"], e["entry"]["text"]) for e in got] == [
            ("edit", "lunch at 2"),
            ("reaction", "👍"),
        ]
        await events.close()


async def test_who_is_typing_is_sent_when_it_changes_and_not_while_it_stays_the_same():
    async with ephemeral_runtime() as rt:
        store, hub = rt.store, FeedHub()
        await open_channel(store)
        working: list[Actor] = []

        async def fake_working(actors: list[Actor]) -> list[Actor]:
            return [a for a in actors if a in working]

        store.working = fake_working  # type: ignore[method-assign]
        events = start(
            me=ME, since={}, store=store, hub=hub, load=lambda: _load(chat()), ping=60
        )
        await events.take(2)  # ready, and how far the agents have read
        assert await events.take(1, timeout=0.4) == []  # nobody is, so nothing is said
        working.append(SCOUT)
        assert await events.take(1) == [
            {"type": "working", "chat": "g", "names": ["Scout"]}
        ]
        assert await events.take(1, timeout=0.4) == []  # still the same: not repeated
        working.clear()
        assert await events.take(1) == [{"type": "working", "chat": "g", "names": []}]
        await events.close()


async def test_someone_new_speaking_makes_the_feed_learn_who_they_are_first():
    async with ephemeral_runtime() as rt:
        store, hub = rt.store, FeedHub()
        await open_channel(store)
        store.channel_observe(Relay(hub).observe)
        quill = Actor("member", "quill@g")
        await store.channel_set_member(CH, Member(agent=quill))
        loaded: list[int] = []

        async def load() -> dict[str, Chat]:
            loaded.append(1)
            names = {str(ME): "You", str(SCOUT): "Scout"}
            if len(loaded) > 1:
                names[str(quill)] = "Quill"
            return {CH: chat(names)}

        events = start(me=ME, since={}, store=store, hub=hub, load=load)
        await events.take(2)
        await store.channel_append(CH, sender=quill, text="hi, I'm new", fresh=True)
        got = await events.entries(1)
        assert got[0]["entry"]["sender"] == "Quill" and len(loaded) == 2
        await events.close()


async def test_a_change_to_your_conversations_is_announced_and_followed():
    async with ephemeral_runtime() as rt:
        store, hub = rt.store, FeedHub()
        await open_channel(store)
        store.channel_observe(Relay(hub).observe)
        theirs = {CH: chat()}

        async def load() -> dict[str, Chat]:
            return dict(theirs)

        events = start(me=ME, since={}, store=store, hub=hub, load=load)
        await events.take(2)
        await store.channel_open(
            "group/h",
            members=[Member(agent=ME, kind=ParticipantKind.HUMAN), Member(agent=SCOUT)],
        )
        theirs["group/h"] = Chat(
            group_id="h", names={str(ME): "You", str(SCOUT): "Scout"}, agents=(SCOUT,)
        )
        hub.dispatch({"t": "chats", "user": ME.key})
        assert (await events.take(1))[0] == {"type": "chats", "chats": ["g", "h"]}
        await store.channel_append(
            "group/h", sender=SCOUT, text="in the new one", fresh=True
        )
        assert (await events.entries(1))[0]["chat"] == "h"
        await events.close()


async def test_a_watcher_that_fell_behind_is_told_to_resync_and_a_quiet_stream_sends_keepalives():
    async with ephemeral_runtime() as rt:
        store, hub = rt.store, FeedHub()
        await open_channel(store)
        events = start(
            me=ME, since={}, store=store, hub=hub, load=lambda: _load(chat()), ping=0.2
        )
        await events.take(2)
        (watcher,) = [w for ws in hub._by_user.values() for w in ws]
        watcher.overflowed = True
        assert (await events.take(1))[0] == {"type": "resync"}
        assert None in await events.take(3)  # nothing said, so it says so
        await events.close()


async def test_the_stream_ends_when_the_client_is_gone():
    async with ephemeral_runtime() as rt:
        store, hub = rt.store, FeedHub()
        await open_channel(store)
        checks = 0

        async def gone() -> bool:
            nonlocal checks
            checks += 1
            return checks > 2

        events = Pump(
            feed_events(
                me=ME,
                since={},
                store=store,
                hub=hub,
                load=lambda: _load(chat()),
                render=render,
                read_position=lambda c: _zero(),
                gone=gone,
                tick=0.05,
            )
        )
        got = await events.take(10, timeout=3)
        assert got[0]["type"] == "ready" and events.ended and hub.watchers() == 0


async def test_how_far_the_agents_have_read_is_sent_when_you_connect_and_again_only_when_it_moves():
    async with ephemeral_runtime() as rt:
        store, hub = rt.store, FeedHub()
        await open_channel(store)
        store.channel_observe(Relay(hub).observe)

        async def idle(actors: list[Actor]) -> list[Actor]:
            return []

        store.working = idle  # type: ignore[method-assign]
        events = start(
            me=ME,
            since={"g": -1},
            store=store,
            hub=hub,
            load=lambda: _load(chat()),
            ping=60,
        )
        assert await events.take(2) == [
            {"type": "ready", "chats": ["g"]},
            {"type": "read", "chat": "g", "by_all": -1},
        ]

        said = await store.channel_append(CH, sender=ME, text="anyone?")
        assert [e["type"] for e in await events.take(2, timeout=1)] == [
            "entry"
        ]  # Scout has not read it, as before: not said again

        await store.channel_mark_read(CH, SCOUT, said.seq)
        assert (
            await events.take(1, timeout=0.4) == []
        )  # nothing happened that tells the feed; it is said with the next thing
        await store.channel_append(CH, sender=SCOUT, text="here", fresh=True)
        got = await events.take(3)
        assert [e["type"] for e in got] == ["entry", "read"] and got[1][
            "by_all"
        ] >= said.seq
        await events.close()
