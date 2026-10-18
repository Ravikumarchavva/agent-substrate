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
    ParticipantKind,
    WakeReason,
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

    async def test_the_breaker_counts_every_speaker_that_is_not_a_person(self, store):
        """Members of a platform's groups are ``member`` actors, not ``agent`` ones: whoever is not a person is an agent for this."""
        a, b = Actor("member", "a@1"), Actor("member", "b@1")
        await store.channel_open(
            CH, members=[Member(agent=a), Member(agent=b)], breaker=2
        )
        await store.channel_append(CH, sender=HUMAN, text="go")
        for sender in (a, b):
            assert not (await store.channel_append(CH, sender=sender, text="x")).paused
        assert (await store.channel_append(CH, sender=a, text="over")).paused

    async def test_a_channels_breaker_and_window_can_be_changed_after_it_is_open(
        self, store
    ):
        await self.open(store, breaker=40)
        await store.channel_configure(CH, breaker=2)
        await store.channel_append(CH, sender=HUMAN, text="go")
        for sender in (SCOUT, QUILL):
            assert not (await store.channel_append(CH, sender=sender, text="x")).paused
        assert (await store.channel_append(CH, sender=SCOUT, text="over")).paused
        await store.channel_configure(CH, engage_s=0)  # leaves the breaker as it is
        await store.channel_append(CH, sender=HUMAN, text="carry on")
        assert (await store.channel_append(CH, sender=SCOUT, text="a")).paused is False
        assert (await store.channel_append(CH, sender=QUILL, text="b")).paused is False
        assert (await store.channel_append(CH, sender=SCOUT, text="c")).paused

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

    async def test_a_page_back_comes_oldest_first_and_stops_at_the_start(self, store):
        await self.open(store)
        for i in range(10):
            await store.channel_append(CH, sender=HUMAN, text=str(i))
        assert [e.text for e in await store.channel_read_before(CH, 10, 4)] == [
            "6",
            "7",
            "8",
            "9",
        ]
        assert [e.text for e in await store.channel_read_before(CH, 6, 4)] == [
            "2",
            "3",
            "4",
            "5",
        ]
        assert [e.text for e in await store.channel_read_before(CH, 2, 4)] == ["0", "1"]
        assert await store.channel_read_before(CH, 0, 4) == []
        assert await store.channel_read_before("missing", 5) == []

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

    # -- why a member was woken, and following a conversation one is part of ------------------------------------------------------------

    async def latest_reason(self, store, agent) -> str | None:
        """Why the member was woken by the newest entry that woke it (its inbox keeps the earlier wakes too)."""
        wakes = sorted(await store.drain(agent), key=lambda w: _data(w)["seq"])
        return _data(wakes[-1])["reason"] if wakes else None

    async def test_a_wake_says_why_the_member_was_woken(self, store):
        await self.open(store, modes={QUILL: Mode.MENTIONS})
        r = await store.channel_append(
            CH, sender=HUMAN, text="@Scout look", mentions=[str(SCOUT)]
        )
        assert await self.latest_reason(store, SCOUT) == WakeReason.DIRECT  # named
        assert (
            await self.latest_reason(store, MAX) == WakeReason.AMBIENT
        )  # listening to everything, not addressed
        assert QUILL not in r.woken  # only wants what is for it

    async def test_everyone_and_a_reply_to_ones_own_entry_are_addressed(self, store):
        await self.open(store, modes={QUILL: Mode.MENTIONS})
        await store.channel_append(CH, sender=HUMAN, text="all", mentions=[EVERYONE])
        assert await self.latest_reason(store, QUILL) == WakeReason.DIRECT
        own = await store.channel_append(CH, sender=QUILL, text="mine")
        await store.channel_append(CH, sender=HUMAN, text="re", reply_to=own.seq)
        assert await self.latest_reason(store, QUILL) == WakeReason.DIRECT

    async def test_a_member_that_was_addressed_follows_the_conversation_without_being_named_again(
        self, store
    ):
        await self.open(store, modes={SCOUT: Mode.MENTIONS, QUILL: Mode.MENTIONS})
        await store.channel_append(
            CH, sender=HUMAN, text="@Scout capital of Japan?", mentions=[str(SCOUT)]
        )
        r = await store.channel_append(
            CH, sender=HUMAN, text="and of Korea?"
        )  # no one is named
        assert (
            SCOUT in r.woken
            and await self.latest_reason(store, SCOUT) == WakeReason.ENGAGED
        )
        assert QUILL not in r.woken  # it was never part of this

    async def test_speaking_engages_a_member_too(self, store):
        await self.open(store, modes={SCOUT: Mode.MENTIONS, QUILL: Mode.MENTIONS})
        await store.channel_append(
            CH, sender=SCOUT, text="Seoul."
        )  # it spoke unprompted
        r = await store.channel_append(CH, sender=HUMAN, text="thanks")
        assert (
            SCOUT in r.woken
            and await self.latest_reason(store, SCOUT) == WakeReason.ENGAGED
        )
        assert QUILL not in r.woken

    async def test_a_window_of_no_time_never_engages(self, store):
        await store.channel_open(
            CH, members=[Member(agent=SCOUT, mode=Mode.MENTIONS)], engage_s=0
        )
        await store.channel_append(
            CH, sender=HUMAN, text="@Scout hi", mentions=[str(SCOUT)]
        )
        r = await store.channel_append(CH, sender=HUMAN, text="and?")
        assert SCOUT not in r.woken

    async def test_a_muted_member_never_follows_a_conversation(self, store):
        await self.open(store, modes={SCOUT: Mode.MUTED})
        await store.channel_append(
            CH, sender=HUMAN, text="@Scout hi", mentions=[str(SCOUT)]
        )
        r = await store.channel_append(CH, sender=HUMAN, text="and?")
        assert SCOUT not in r.woken

    # -- people are members too: they read and are read to, but are never woken ----------------------------------------------------------

    async def open_with_person(self, store, **kw) -> None:
        await store.channel_open(
            CH,
            members=[
                Member(agent=HUMAN, kind=ParticipantKind.HUMAN),
                Member(agent=SCOUT),
                Member(agent=QUILL),
            ],
            **kw,
        )

    async def test_a_person_in_a_channel_is_never_woken_and_never_gets_a_run(
        self, store
    ):
        await self.open_with_person(store)
        r = await store.channel_append(CH, sender=SCOUT, text="hello")
        assert HUMAN not in r.woken and set(r.woken) == {QUILL}
        assert await store.drain(HUMAN) == []
        r = await store.channel_append(CH, sender=HUMAN, text="hi")
        assert set(r.woken) == {SCOUT, QUILL}
        members = {m.agent: m for m in await store.channel_members(CH)}
        assert (
            members[HUMAN].kind == ParticipantKind.HUMAN
            and members[HUMAN].inbox is None
        )
        assert (
            members[SCOUT].kind == ParticipantKind.AGENT
            and members[SCOUT].inbox == SCOUT
        )

    async def test_a_member_can_have_an_inbox_other_than_its_own_name(self, store):
        inbox = Actor("chat", "scout@1")
        await store.channel_open(
            CH, members=[Member(agent=Actor("agent", "scout"), inbox=inbox)]
        )
        r = await store.channel_append(CH, sender=HUMAN, text="hi")
        assert r.woken == (inbox,)
        assert await store.drain(inbox)

    async def test_unread_counts_what_others_wrote_after_the_cursor(self, store):
        await self.open_with_person(store)
        await store.channel_append(CH, sender=SCOUT, text="a")  # 0
        await store.channel_append(CH, sender=QUILL, text="b")  # 1
        await store.channel_append(
            CH, sender=HUMAN, text="mine"
        )  # 2: own entries are never unread
        [head] = await store.channel_heads([CH], HUMAN)
        assert (
            head.unread == 0 and head.latest is not None and head.latest.text == "mine"
        )
        await store.channel_append(CH, sender=SCOUT, text="c")
        await store.channel_append(CH, sender=SCOUT, text="d")
        [head] = await store.channel_heads([CH], HUMAN)
        assert head.unread == 2
        await store.channel_mark_read(CH, HUMAN, 3)
        [head] = await store.channel_heads([CH], HUMAN)
        assert head.unread == 1
        await store.channel_mark_read(CH, HUMAN, 4)
        assert (await store.channel_heads([CH], HUMAN))[0].unread == 0

    async def test_unread_is_not_capped_and_ignores_system_notices_edits_and_reactions(
        self, store
    ):
        await self.open_with_person(store)
        for i in range(250):
            await store.channel_append(CH, sender=SCOUT, text=str(i), fresh=True)
        entry = (await store.channel_last(CH, 1))[0]
        await store.channel_react(CH, entry.seq, QUILL, "👍")
        await store.channel_edit(CH, entry.seq, SCOUT, "edited")
        [head] = await store.channel_heads([CH], HUMAN)
        assert head.unread == 250

    async def test_heads_cover_many_channels_and_a_missing_one_is_empty(self, store):
        await self.open_with_person(store)
        await store.channel_open(
            "group/2",
            members=[
                Member(agent=HUMAN, kind=ParticipantKind.HUMAN),
                Member(agent=SCOUT),
            ],
        )
        await store.channel_append(CH, sender=SCOUT, text="one")
        heads = {
            h.channel: h
            for h in await store.channel_heads([CH, "group/2", "nope"], HUMAN)
        }
        assert (
            heads[CH].unread == 1
            and heads["group/2"].latest is None
            and heads["group/2"].unread == 0
        )
        assert "nope" not in heads

    async def test_delivered_only_moves_forward_and_starts_unset(self, store):
        await self.open_with_person(store)
        for i in range(3):
            await store.channel_append(CH, sender=SCOUT, text=str(i))
        assert {m.agent: m.delivered for m in await store.channel_members(CH)}[
            HUMAN
        ] == -1
        await store.channel_mark_delivered(CH, HUMAN, 2)
        await store.channel_mark_delivered(CH, HUMAN, 0)
        assert {m.agent: m.delivered for m in await store.channel_members(CH)}[
            HUMAN
        ] == 2

    async def test_a_member_added_later_remembers_where_it_joined(self, store):
        await self.open_with_person(store)
        await store.channel_append(CH, sender=HUMAN, text="before")
        await store.channel_append(CH, sender=HUMAN, text="before too")
        await store.channel_set_member(CH, Member(agent=MAX))
        await store.channel_set_member(
            CH, Member(agent=MAX, mode=Mode.MENTIONS)
        )  # a mode change keeps it
        members = {m.agent: m for m in await store.channel_members(CH)}
        assert (
            members[SCOUT].joined_seq == 0
            and members[MAX].joined_seq == 2
            and members[MAX].mode == Mode.MENTIONS
        )

    # -- every entry has an id; a person can edit, delete and react ----------------------------------------------------------------------

    async def test_every_entry_has_a_unique_id_and_a_repeat_returns_the_same_one(
        self, store
    ):
        await self.open(store)
        a = await store.channel_append(CH, sender=SCOUT, text="x", dedup_key="k")
        b = await store.channel_append(CH, sender=SCOUT, text="x", dedup_key="k")
        c = await store.channel_append(CH, sender=HUMAN, text="y")
        assert a.id and a.id == b.id and c.id and c.id != a.id
        assert [e.id for e in await store.channel_read(CH)] == [a.id, c.id]

    async def test_an_edit_replaces_the_text_keeps_the_old_one_and_wakes_no_one(
        self, store
    ):
        await self.open_with_person(store)
        sent = await store.channel_append(
            CH, sender=HUMAN, text="lunch at 1", mentions=[str(SCOUT)]
        )
        inboxes = (len(await store.drain(SCOUT)), len(await store.drain(QUILL)))
        assert inboxes == (1, 1)
        done = await store.channel_edit(CH, sent.seq, HUMAN, "lunch at 2")
        assert done is not None and done > sent.seq
        original, marker = await store.channel_read(CH)
        assert (
            original.text == "lunch at 2"
            and original.edited_at is not None
            and original.id == sent.id
        )
        assert (
            marker.kind == EntryKind.EDIT
            and marker.reply_to == sent.seq
            and marker.text == "lunch at 2"
        )
        assert marker.data == {
            "previous": "lunch at 1"
        }  # nothing that was said is lost
        assert (len(await store.drain(SCOUT)), len(await store.drain(QUILL))) == inboxes

    async def test_only_the_sender_can_edit_or_delete_and_not_a_marker(self, store):
        await self.open_with_person(store)
        sent = await store.channel_append(CH, sender=HUMAN, text="mine")
        assert await store.channel_edit(CH, sent.seq, SCOUT, "hijack") is None
        assert await store.channel_tombstone(CH, sent.seq, SCOUT) is None
        assert await store.channel_edit(CH, 99, HUMAN, "nothing there") is None
        marker = await store.channel_edit(CH, sent.seq, HUMAN, "ok")
        assert await store.channel_edit(CH, marker, HUMAN, "again") is None
        assert (await store.channel_read(CH))[0].text == "ok"

    async def test_a_deleted_entry_is_blanked_everywhere_including_its_edit_history(
        self, store
    ):
        await self.open_with_person(store)
        sent = await store.channel_append(
            CH, sender=HUMAN, text="secret", data={"attachments": [{"name": "a.pdf"}]}
        )
        await store.channel_edit(CH, sent.seq, HUMAN, "secret v2")
        gone = await store.channel_tombstone(CH, sent.seq, HUMAN)
        assert gone is not None
        entries = {e.seq: e for e in await store.channel_read(CH)}
        assert (
            entries[sent.seq].text == ""
            and entries[sent.seq].deleted_at is not None
            and entries[sent.seq].data == {}
        )
        assert all(
            "secret" not in e.text and "secret" not in str(e.data)
            for e in entries.values()
        )
        assert (
            entries[gone].kind == EntryKind.TOMBSTONE
            and entries[gone].reply_to == sent.seq
        )
        assert await store.channel_edit(CH, sent.seq, HUMAN, "revive") is None

    async def test_a_reaction_is_one_per_person_replaceable_and_removable(self, store):
        await self.open_with_person(store)
        sent = await store.channel_append(CH, sender=SCOUT, text="answer")
        await store.channel_react(CH, sent.seq, HUMAN, "👍")
        await store.channel_react(CH, sent.seq, HUMAN, "❤️")  # replaces the first
        await store.channel_react(CH, sent.seq, QUILL, "👍")
        assert await store.channel_reactions(CH, [sent.seq]) == {
            sent.seq: {str(HUMAN): "❤️", str(QUILL): "👍"}
        }
        await store.channel_react(CH, sent.seq, HUMAN, "")  # takes it back
        assert await store.channel_reactions(CH, [sent.seq]) == {
            sent.seq: {str(QUILL): "👍"}
        }
        assert await store.channel_reactions(CH, [123]) == {}
        assert await store.channel_react(CH, 99, HUMAN, "👍") is None

    # -- depth follows what caused an entry, so a routine or a person starts afresh ---------------------------------------------------------

    async def test_a_fresh_entry_starts_a_new_chain_and_never_trips_the_breaker(
        self, store
    ):
        await self.open_with_person(store, breaker=3)
        for i in range(10):  # ten routine reports with no person in between
            r = await store.channel_append(
                CH, sender=SCOUT, text=f"report {i}", fresh=True
            )
            assert r.seq is not None and not r.paused
        assert [e.depth for e in await store.channel_read(CH)] == [0] * 10

    async def test_a_reply_is_one_deeper_than_what_it_answers(self, store):
        await self.open_with_person(store)
        await store.channel_append(CH, sender=HUMAN, text="q")  # 0, depth 0
        await store.channel_append(
            CH, sender=SCOUT, text="a"
        )  # 1: by default it answers the latest entry, so depth 1
        await store.channel_append(
            CH, sender=QUILL, text="b", cause_seq=0
        )  # answers the question, not Scout: depth 1 too
        await store.channel_append(CH, sender=MAX, text="c")  # answers b
        assert [e.depth for e in await store.channel_read(CH)] == [0, 1, 1, 2]

    # -- running out of budget is said once, not silently ---------------------------------------------------------------------------------

    async def test_a_member_out_of_budget_is_announced_once_and_not_woken(self, store):
        from substrate.runtime.store import ExecutionBudget

        await self.open_with_person(store)
        await store.account_limit(f"agent:{SCOUT}", ExecutionBudget(max_tokens=0))
        r1 = await store.channel_append(CH, sender=HUMAN, text="one")
        r2 = await store.channel_append(CH, sender=HUMAN, text="two")
        assert SCOUT not in r1.woken and SCOUT not in r2.woken and QUILL in r2.woken
        notices = [
            e for e in await store.channel_read(CH) if e.kind == EntryKind.SYSTEM
        ]
        assert len(notices) == 1 and "scout" in notices[0].text.lower()
        await store.account_limit(
            f"agent:{SCOUT}", ExecutionBudget(max_tokens=1_000)
        )  # topped up
        r3 = await store.channel_append(CH, sender=HUMAN, text="three")
        assert SCOUT in r3.woken

    # -- an observer hears about what was committed ----------------------------------------------------------------------------------------

    async def test_an_observer_hears_every_committed_change_but_not_a_refused_post(
        self, store
    ):
        heard: list[tuple[str, int, EntryKind]] = []

        async def observer(change) -> None:
            heard.append((change.channel, change.seq, change.kind))

        store.channel_observe(observer)
        await self.open_with_person(store, breaker=1)
        sent = await store.channel_append(CH, sender=HUMAN, text="x")
        await store.channel_edit(CH, sent.seq, HUMAN, "y")
        await store.channel_react(CH, sent.seq, SCOUT, "👍")
        await store.channel_append(CH, sender=SCOUT, text="a")  # depth 1: fine
        refused = await store.channel_append(
            CH, sender=QUILL, text="b"
        )  # depth 2 > 1: pauses, writes a notice
        assert refused.paused
        await store.channel_append(CH, sender=HUMAN, text="x", dedup_key="d")
        await store.channel_append(
            CH, sender=HUMAN, text="x", dedup_key="d"
        )  # a repeat changes nothing
        assert [k for _, _, k in heard] == [
            EntryKind.MESSAGE,
            EntryKind.EDIT,
            EntryKind.REACTION,
            EntryKind.MESSAGE,
            EntryKind.SYSTEM,
            EntryKind.MESSAGE,
        ]
        assert [s for _, s, _ in heard] == sorted(s for _, s, _ in heard)

    async def test_a_failing_observer_does_not_stop_the_append(self, store):
        async def broken(change) -> None:
            raise RuntimeError("down")

        store.channel_observe(broken)
        await self.open(store)
        assert (await store.channel_append(CH, sender=HUMAN, text="x")).seq == 0
