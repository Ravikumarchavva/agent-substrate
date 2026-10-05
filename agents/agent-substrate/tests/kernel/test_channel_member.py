"""Agents in a channel: each one decides whether to speak; none answers twice; a burst is one turn."""

from __future__ import annotations

import asyncio

from substrate.agents.channel import PASS, ChannelMemberAgent, ChannelMemberConfig
from substrate.context import ContextConfig
from substrate.runtime import EVERYONE, Member, Mode
from substrate.testing.runtime import ephemeral_runtime
from substrate.testing.scripted import ScriptedModel
from substrate.types import Actor
from tests._stores import fs_history

HUMAN = Actor("user", "ravi")
G = "group/1"


def member(
    name: str, model: ScriptedModel, names: dict[str, str]
) -> ChannelMemberAgent:
    return ChannelMemberAgent(
        "member",
        session_id=f"{name}@g",
        model=model,
        context=ContextConfig(history=fs_history()),
        system_instructions=f"You are {name}.",
        channel=ChannelMemberConfig(names=names, debounce_s=0.05),
    )


def last_text(messages) -> str:
    return messages[-1].text


async def settle(store, *, quiet: float = 0.6, timeout: float = 15.0) -> None:
    """Until nothing is pending or running for a moment."""
    loop = asyncio.get_event_loop()
    end = loop.time() + timeout
    calm_since = None
    while loop.time() < end:
        s = await store.stats()
        if s.pending == 0 and s.running == 0:
            calm_since = calm_since or loop.time()
            if loop.time() - calm_since >= quiet:
                return
        else:
            calm_since = None
        await asyncio.sleep(0.05)
    raise AssertionError("the channel never went quiet")


def roster(*agents: Actor) -> dict[str, str]:
    names = {str(HUMAN): "Ravi"}
    for a in agents:
        names[str(a)] = a.key.split("@")[0].title()
    return names


async def test_each_member_chooses_whether_to_speak() -> None:
    scout, quill = Actor("member", "scout@g"), Actor("member", "quill@g")
    names = roster(scout, quill)
    scout_model = ScriptedModel("The answer is 4.")
    quill_model = ScriptedModel(PASS)
    async with ephemeral_runtime() as rt:
        await rt.register(member("scout", scout_model, names))
        await rt.register(member("quill", quill_model, names))
        store = rt.store
        await store.channel_open(G, members=[Member(agent=scout), Member(agent=quill)])
        await store.channel_append(G, sender=HUMAN, text="what is 2+2?")
        await settle(store)
        entries = await store.channel_read(G)

    assert [(e.sender.key, e.text) for e in entries] == [
        ("ravi", "what is 2+2?"),
        ("scout@g", "The answer is 4."),
    ]
    # Quill looks twice: at the question, and at Scout's answer to it. It stays silent both times.
    assert quill_model._calls == 2 and PASS not in [e.text for e in entries]


async def test_two_members_answering_at_once_do_not_both_post() -> None:
    scout, quill = Actor("member", "scout@g"), Actor("member", "quill@g")
    names = roster(scout, quill)

    def decide(messages) -> str:
        # The second look shows what the other said; a member that sees an answer has nothing to add.
        seen = last_text(messages)
        return PASS if "Scout:" in seen or "Quill:" in seen else "It is 4."

    scout_model, quill_model = ScriptedModel(decide), ScriptedModel(decide)
    async with ephemeral_runtime() as rt:
        await rt.register(member("scout", scout_model, names))
        await rt.register(member("quill", quill_model, names))
        store = rt.store
        await store.channel_open(G, members=[Member(agent=scout), Member(agent=quill)])
        await store.channel_append(G, sender=HUMAN, text="what is 2+2?")
        await settle(store)
        entries = await store.channel_read(G)

    answers = [e for e in entries if e.sender != HUMAN]
    assert len(answers) == 1, [(e.sender.key, e.text) for e in entries]


async def test_a_burst_of_messages_is_one_turn_for_each_member() -> None:
    scout = Actor("member", "scout@g")
    names = roster(scout)
    model = ScriptedModel(lambda m: PASS)
    async with ephemeral_runtime() as rt:
        await rt.register(member("scout", model, names))
        store = rt.store
        await store.channel_open(G, members=[Member(agent=scout)])
        for i in range(5):
            await store.channel_append(G, sender=HUMAN, text=f"msg {i}")
        await settle(store)

    assert model._calls == 1
    digest = last_text(model.seen[0])
    assert all(f"msg {i}" in digest for i in range(5))


async def test_a_mention_wakes_a_member_that_only_listens_for_mentions() -> None:
    scout, quill = Actor("member", "scout@g"), Actor("member", "quill@g")
    names = roster(scout, quill)
    scout_model, quill_model = ScriptedModel(PASS), ScriptedModel("Here.")
    async with ephemeral_runtime() as rt:
        await rt.register(member("scout", scout_model, names))
        await rt.register(member("quill", quill_model, names))
        store = rt.store
        await store.channel_open(
            G,
            members=[Member(agent=scout), Member(agent=quill, mode=Mode.MENTIONS)],
        )
        await store.channel_append(G, sender=HUMAN, text="chatter")
        await settle(store)
        assert quill_model._calls == 0
        await store.channel_append(
            G, sender=HUMAN, text="@Quill you there?", mentions=[str(quill)]
        )
        await settle(store)
        entries = await store.channel_read(G)

    assert quill_model._calls == 1
    assert entries[-1].sender == quill and "@everyone" not in entries[-1].text
    assert EVERYONE not in entries[-1].mentions


async def test_an_answer_that_names_a_member_mentions_it() -> None:
    scout, quill = Actor("member", "scout@g"), Actor("member", "quill@g")
    names = roster(scout, quill)
    async with ephemeral_runtime() as rt:
        await rt.register(
            member("scout", ScriptedModel("@Quill can you check?"), names)
        )
        await rt.register(member("quill", ScriptedModel(PASS), names))
        store = rt.store
        await store.channel_open(
            G, members=[Member(agent=scout), Member(agent=quill, mode=Mode.MENTIONS)]
        )
        await store.channel_append(G, sender=HUMAN, text="help", mentions=[str(scout)])
        await settle(store)
        entries = await store.channel_read(G)

    assert entries[1].mentions == (str(quill),)


def test_mentions_match_whole_names_in_any_case() -> None:
    from substrate.runtime import mentions_in

    names = {"agent/a": "Scout", "agent/b": "Scout Two", "agent/c": "Max"}
    assert mentions_in("hey @scout, look", names) == ["agent/a"]
    assert mentions_in("@Scout Two please", names) == ["agent/a", "agent/b"]
    assert mentions_in("@maxine", names) == []
    assert mentions_in("@everyone @Max", names, exclude="agent/c") == [EVERYONE]


async def test_the_digest_says_who_each_message_is_for() -> None:
    scout, quill = Actor("member", "scout@g"), Actor("member", "quill@g")
    names = roster(scout, quill)
    model = ScriptedModel(PASS)
    async with ephemeral_runtime() as rt:
        await rt.register(member("scout", ScriptedModel(PASS), names))
        await rt.register(member("quill", model, names))
        store = rt.store
        await store.channel_open(G, members=[Member(agent=scout), Member(agent=quill)])
        await store.channel_append(G, sender=HUMAN, text="@Scout capital of Japan?", mentions=[str(scout)])
        await settle(store)
    assert "(to Scout)" in last_text(model.seen[0])
