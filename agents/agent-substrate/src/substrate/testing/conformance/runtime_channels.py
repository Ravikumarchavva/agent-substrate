"""Channel tests of the ``RuntimeStore`` conformance suite (mixed into ``RuntimeStoreConformance``).

A wake is a real delivery, so these tests read the woken member's inbox (``drain``) the way
its run would."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from substrate.runtime.channel import (
    EVERYONE,
    EntryKind,
    Member,
    Mode,
)
from substrate.types.identity import Actor

CH = "group/1"
HUMAN = Actor("user", "ravi")
SCOUT = Actor("agent", "scout@1")
QUILL = Actor("agent", "quill@1")
MAX = Actor("agent", "max@1")


def _data(m) -> dict:
    return dict(m.payload.data)  # type: ignore[union-attr]


class ChannelTests:
    @pytest.fixture
    async def store(self):  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    async def open(self, store, *, breaker: int = 40, modes=None) -> None:
        modes = modes or {}
        await store.channel_open(
            CH,
            members=[
                Member(agent=a, mode=modes.get(a, Mode.ALL))
                for a in (SCOUT, QUILL, MAX)
            ],
            breaker=breaker,
        )

    async def test_entries_are_numbered_from_zero_in_the_order_appended(self, store):
        await self.open(store)
        for i in range(3):
            r = await store.channel_append(CH, sender=HUMAN, text=f"m{i}")
            assert r.seq == i
        assert [e.text for e in await store.channel_read(CH)] == ["m0", "m1", "m2"]
        assert [e.seq for e in await store.channel_read(CH, after=0)] == [1, 2]

    async def test_concurrent_writers_get_distinct_contiguous_seqs(self, store):
        await self.open(store)
        results = await asyncio.gather(
            *(
                store.channel_append(CH, sender=HUMAN if i % 2 else SCOUT, text=str(i))
                for i in range(20)
            )
        )
        assert sorted(r.seq for r in results) == list(range(20))
        assert [e.seq for e in await store.channel_read(CH)] == list(range(20))

    async def test_an_entry_wakes_every_member_but_its_sender(self, store):
        await self.open(store)
        r = await store.channel_append(CH, sender=HUMAN, text="hi all")
        assert set(r.woken) == {SCOUT, QUILL, MAX}
        for a in (SCOUT, QUILL, MAX):
            wakes = await store.drain(a)
            assert [(_data(w)["channel"], _data(w)["seq"]) for w in wakes] == [(CH, 0)]
        r = await store.channel_append(CH, sender=SCOUT, text="hello")
        assert set(r.woken) == {QUILL, MAX}
        assert await store.drain(SCOUT) == [
            w for w in await store.drain(SCOUT) if _data(w)["seq"] == 0
        ]

    async def test_a_wake_starts_a_run_for_an_idle_member(self, store):
        await self.open(store)
        await store.channel_append(CH, sender=HUMAN, text="hi")
        assert (await store.stats()).pending >= 3

    async def test_a_mentions_only_member_wakes_for_mentions_everyone_and_replies(
        self, store
    ):
        await self.open(store, modes={QUILL: Mode.MENTIONS})
        r = await store.channel_append(CH, sender=HUMAN, text="plain")
        assert QUILL not in r.woken
        r = await store.channel_append(
            CH, sender=HUMAN, text="@quill", mentions=[str(QUILL)]
        )
        assert QUILL in r.woken
        r = await store.channel_append(
            CH, sender=HUMAN, text="all", mentions=[EVERYONE]
        )
        assert QUILL in r.woken
        own = await store.channel_append(CH, sender=QUILL, text="mine")
        r = await store.channel_append(CH, sender=HUMAN, text="re", reply_to=own.seq)
        assert QUILL in r.woken

    async def test_a_muted_member_wakes_only_when_named(self, store):
        await self.open(store, modes={MAX: Mode.MUTED})
        r = await store.channel_append(
            CH, sender=HUMAN, text="all", mentions=[EVERYONE]
        )
        assert MAX not in r.woken
        r = await store.channel_append(CH, sender=HUMAN, text="x", mentions=[str(MAX)])
        assert MAX in r.woken

    async def test_a_post_is_stale_when_others_spoke_after_what_the_poster_read(
        self, store
    ):
        await self.open(store)
        await store.channel_append(CH, sender=HUMAN, text="question")  # 0
        await store.channel_append(CH, sender=SCOUT, text="answer")  # 1
        r = await store.channel_append(CH, sender=QUILL, text="same", read_up_to=0)
        assert r.stale and r.seq is None and r.latest == 1
        assert len(await store.channel_read(CH)) == 2
        r = await store.channel_append(CH, sender=QUILL, text="more", read_up_to=1)
        assert r.seq == 2 and not r.stale

    async def test_one_of_two_racing_posts_is_refused_as_stale(self, store):
        await self.open(store)
        await store.channel_append(CH, sender=HUMAN, text="q")
        a, b = await asyncio.gather(
            store.channel_append(CH, sender=SCOUT, text="a", read_up_to=0),
            store.channel_append(CH, sender=QUILL, text="b", read_up_to=0),
        )
        assert sorted([a.stale, b.stale]) == [False, True]
        assert len(await store.channel_read(CH)) == 2

    async def test_a_members_own_posts_do_not_make_its_next_post_stale(self, store):
        await self.open(store)
        await store.channel_append(CH, sender=HUMAN, text="q")
        await store.channel_append(CH, sender=SCOUT, text="1", read_up_to=0)
        r = await store.channel_append(CH, sender=SCOUT, text="2", read_up_to=0)
        assert r.seq == 2 and not r.stale

    async def test_cursors_only_move_forward(self, store):
        await self.open(store)
        for i in range(4):
            await store.channel_append(CH, sender=HUMAN, text=str(i))
        await store.channel_mark_read(CH, SCOUT, 3)
        await store.channel_mark_read(CH, SCOUT, 1)
        cursors = {m.agent: m.cursor for m in await store.channel_members(CH)}
        assert cursors[SCOUT] == 3 and cursors[QUILL] == -1

    async def test_the_sender_has_read_what_it_wrote(self, store):
        await self.open(store)
        r = await store.channel_append(CH, sender=SCOUT, text="x")
        cursors = {m.agent: m.cursor for m in await store.channel_members(CH)}
        assert cursors[SCOUT] == r.seq

    async def test_depth_counts_agent_entries_since_a_human_spoke(self, store):
        await self.open(store)
        await store.channel_append(CH, sender=HUMAN, text="h")
        await store.channel_append(CH, sender=SCOUT, text="a")
        await store.channel_append(CH, sender=QUILL, text="b")
        await store.channel_append(CH, sender=HUMAN, text="h2")
        assert [e.depth for e in await store.channel_read(CH)] == [0, 1, 2, 0]

    async def test_the_breaker_pauses_agent_only_talk_until_a_human_speaks(self, store):
        await self.open(store, breaker=3)
        await store.channel_append(CH, sender=HUMAN, text="go")
        for i in range(3):
            r = await store.channel_append(
                CH, sender=SCOUT if i % 2 else QUILL, text=str(i)
            )
            assert r.seq is not None and not r.paused
        r = await store.channel_append(CH, sender=SCOUT, text="over")
        assert r.paused and r.seq is None and r.woken == ()
        entries = await store.channel_read(CH)
        assert entries[-1].kind == EntryKind.SYSTEM
        r = await store.channel_append(CH, sender=SCOUT, text="still")
        assert r.paused
        assert (
            len([e for e in await store.channel_read(CH) if e.kind == EntryKind.SYSTEM])
            == 1
        )
        await store.channel_append(CH, sender=HUMAN, text="carry on")
        r = await store.channel_append(CH, sender=SCOUT, text="back")
        assert r.seq is not None and not r.paused

    async def test_removing_a_member_stops_its_wakes(self, store):
        await self.open(store)
        await store.channel_remove_member(CH, MAX)
        r = await store.channel_append(CH, sender=HUMAN, text="x")
        assert MAX not in r.woken
        assert MAX not in [m.agent for m in await store.channel_members(CH)]

    async def test_reopening_keeps_the_log_and_adds_members(self, store):
        await self.open(store)
        await store.channel_append(CH, sender=HUMAN, text="x")
        await store.channel_open(CH, members=[Member(agent=Actor("agent", "new@1"))])
        assert len(await store.channel_read(CH)) == 1
        assert len(await store.channel_members(CH)) == 4

    async def test_a_repeated_append_with_the_same_key_lands_once(self, store):
        await self.open(store)
        first = await store.channel_append(CH, sender=SCOUT, text="x", dedup_key="k")
        again = await store.channel_append(CH, sender=SCOUT, text="x", dedup_key="k")
        assert first.seq == again.seq == 0
        assert len(await store.channel_read(CH)) == 1

    async def test_the_last_entries_come_back_oldest_first(self, store):
        await self.open(store)
        for i in range(5):
            await store.channel_append(CH, sender=HUMAN, text=str(i))
        assert [e.text for e in await store.channel_last(CH, 2)] == ["3", "4"]
        assert await store.channel_last("missing") == []

    async def test_a_deleted_channel_is_gone_with_its_entries_and_members(self, store):
        await self.open(store)
        await store.channel_append(CH, sender=HUMAN, text="x")
        await store.channel_delete(CH)
        assert await store.channel_read(CH) == []
        assert await store.channel_members(CH) == []
        await store.channel_open(CH, members=[Member(agent=SCOUT)])
        assert (await store.channel_append(CH, sender=HUMAN, text="y")).seq == 0

    async def test_a_waiter_returns_when_an_entry_arrives_and_times_out_when_none_does(
        self, store
    ):
        await self.open(store)
        assert await store.channel_wait(CH, -1, 0.1) is False
        waiter = asyncio.create_task(store.channel_wait(CH, -1, 5.0))
        await asyncio.sleep(0.05)
        await store.channel_append(CH, sender=HUMAN, text="x")
        assert await asyncio.wait_for(waiter, 3.0) is True
        assert await store.channel_wait(CH, 0, 0.1) is False

    async def test_erasing_an_actor_removes_its_runs_mail_and_budget_but_not_others(
        self, store
    ):
        from substrate.runtime.store import Commit, NewEntry, Spend

        await self.open(store)
        await store.channel_append(CH, sender=HUMAN, text="hi")
        leases = await store.lease(
            worker_id="w",
            capacity=5,
            lease_s=30,
            now=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
        for lease in leases:
            await store.commit(
                lease,
                Commit(
                    entries=(NewEntry(kind="llm.call", spend=Spend(tokens=5, turns=1)),)
                ),
            )
        assert (await store.account_spend(f"agent:{SCOUT}")).tokens == 5
        removed = await store.erase(tenant="default", agent=SCOUT)
        assert removed == 1
        assert (await store.account_spend(f"agent:{SCOUT}")).tokens == 0
        assert (await store.account_spend(f"agent:{QUILL}")).tokens == 5
        assert await store.erase(tenant="default", agent=SCOUT) == 0

    async def test_a_deleted_channel_takes_its_budget_account_with_it(self, store):
        await self.open(store)
        await store.channel_append(CH, sender=HUMAN, text="hi")
        leases = await store.lease(
            worker_id="w",
            capacity=1,
            lease_s=30,
            now=datetime(2030, 1, 1, tzinfo=timezone.utc),
        )
        from substrate.runtime.store import Commit, NewEntry, Spend

        await store.commit(
            leases[0],
            Commit(
                entries=(NewEntry(kind="llm.call", spend=Spend(tokens=9, turns=1)),)
            ),
        )
        assert (await store.account_spend(f"channel:{CH}")).tokens == 9
        await store.channel_delete(CH)
        assert (await store.account_spend(f"channel:{CH}")).tokens == 0

    async def test_erasing_a_tenant_removes_its_channels_and_leaves_other_tenants(
        self, store
    ):
        await store.channel_open("a", tenant="t1", members=[Member(agent=SCOUT)])
        await store.channel_open("b", tenant="t2", members=[Member(agent=QUILL)])
        await store.channel_append("a", sender=HUMAN, text="x")
        await store.channel_append("b", sender=HUMAN, text="y")
        await store.erase(tenant="t1")
        assert (
            await store.channel_read("a") == []
            and await store.channel_members("a") == []
        )
        assert [e.text for e in await store.channel_read("b")] == ["y"]

    async def test_structured_data_rides_with_an_entry(self, store):
        await self.open(store)
        await store.channel_append(
            CH,
            sender=HUMAN,
            text="see file",
            data={"attachments": [{"name": "a.pdf", "size": 3}]},
        )
        await store.channel_append(CH, sender=HUMAN, text="plain")
        first, second = await store.channel_read(CH)
        assert (
            first.data == {"attachments": [{"name": "a.pdf", "size": 3}]}
            and second.data == {}
        )
