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
        await store.channel_append(
            G, sender=HUMAN, text="@Scout capital of Japan?", mentions=[str(scout)]
        )
        await settle(store)
    assert "(to Scout)" in last_text(model.seen[0])


async def test_the_digest_shows_what_was_attached_and_what_it_says() -> None:
    scout = Actor("member", "scout@g")
    model = ScriptedModel(PASS)
    async with ephemeral_runtime() as rt:
        await rt.register(member("scout", model, roster(scout)))
        store = rt.store
        await store.channel_open(G, members=[Member(agent=scout)])
        await store.channel_append(
            G,
            sender=HUMAN,
            text="here is the budget",
            data={
                "attachments": [
                    {
                        "name": "budget.csv",
                        "size": 2048,
                        "excerpt": "item,eur\nflights,900",
                        "truncated": True,
                    },
                    {"name": "photo.png", "size": 9},
                ]
            },
        )
        await settle(store)
    seen = last_text(model.seen[0])
    assert "(attached: budget.csv, 2048 bytes)" in seen
    assert (
        "contents of budget.csv [only the start is shown]:" in seen
        and "| flights,900" in seen
    )
    assert "(attached: photo.png, 9 bytes)\n    (no text could be read from it)" in seen


# -- senses and hands: seeing what is shared, and sharing what one made ------------------------------------------------------------------

from substrate.models import Modality, ModelCapabilities  # noqa: E402
from substrate.testing.scripted import ToolCall  # noqa: E402
from substrate.types import MediaBlock, TextBlock, ToolResultBlock  # noqa: E402


def seeing(model: ScriptedModel) -> ScriptedModel:
    model.capabilities = ModelCapabilities(
        model_id="sees", input_modalities=frozenset({Modality.TEXT, Modality.IMAGE})
    )
    return model


def member_with(
    name: str, model: ScriptedModel, names: dict[str, str], **config
) -> ChannelMemberAgent:
    return ChannelMemberAgent(
        "member",
        session_id=f"{name}@g",
        model=model,
        context=ContextConfig(history=fs_history()),
        system_instructions=f"You are {name}.",
        channel=ChannelMemberConfig(names=names, debounce_s=0.05, **config),
    )


def picture(name: str) -> dict:
    return {"name": name, "size": 3, "mime": "image/png", "key": f"k/{name}"}


async def loader(attachment) -> MediaBlock | None:
    return MediaBlock.image(
        data=b"\x89PNG-" + str(attachment["name"]).encode(),
        media_type="image/png",
        filename=attachment["name"],
    )


def media_in(messages) -> list[str]:
    return [
        b.filename for m in messages for b in m.content if isinstance(b, MediaBlock)
    ]


async def test_a_picture_shared_in_the_group_is_put_in_front_of_a_model_that_can_see() -> (
    None
):
    scout = Actor("member", "scout@g")
    model = seeing(ScriptedModel(PASS))
    async with ephemeral_runtime() as rt:
        await rt.register(member_with("scout", model, roster(scout), media=loader))
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(
            G,
            sender=HUMAN,
            text="what is this?",
            data={"attachments": [picture("cat.png")]},
        )
        await settle(rt.store)
    assert media_in(model.seen[0]) == ["cat.png"]
    assert "(attached: cat.png" in last_text(model.seen[0])


async def test_a_model_that_cannot_see_is_told_so_and_nothing_is_loaded_for_it() -> (
    None
):
    scout = Actor("member", "scout@g")
    model = ScriptedModel(PASS)  # text only
    loaded: list[str] = []

    async def spy(attachment):
        loaded.append(attachment["name"])
        return await loader(attachment)

    async with ephemeral_runtime() as rt:
        await rt.register(member_with("scout", model, roster(scout), media=spy))
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(
            G,
            sender=HUMAN,
            text="what is this?",
            data={"attachments": [picture("cat.png")]},
        )
        await settle(rt.store)
    assert loaded == [] and media_in(model.seen[0]) == []
    assert "you cannot see it" in last_text(model.seen[0])


async def test_only_the_first_few_pictures_of_a_digest_are_shown() -> None:
    scout = Actor("member", "scout@g")
    model = seeing(ScriptedModel(PASS))
    async with ephemeral_runtime() as rt:
        await rt.register(
            member_with("scout", model, roster(scout), media=loader, max_media=2)
        )
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(
            G,
            sender=HUMAN,
            text="photos",
            data={"attachments": [picture(f"{i}.png") for i in range(4)]},
        )
        await settle(rt.store)
    assert media_in(model.seen[0]) == ["0.png", "1.png"]
    assert "not shown" in last_text(model.seen[0])


async def test_history_keeps_a_note_of_a_picture_not_its_bytes() -> None:
    """The next turn loads what was said before: a picture must not be carried (and paid for) again with every message after it."""
    scout = Actor("member", "scout@g")
    model = seeing(ScriptedModel("A cat.", PASS))
    async with ephemeral_runtime() as rt:
        await rt.register(member_with("scout", model, roster(scout), media=loader))
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(
            G,
            sender=HUMAN,
            text="what is this?",
            data={"attachments": [picture("cat.png")]},
        )
        await settle(rt.store)
        await rt.store.channel_append(G, sender=HUMAN, text="and now?")
        await settle(rt.store)
    assert media_in(model.seen[0]) == ["cat.png"]
    later = model.seen[-1]
    assert (
        media_in(later[:-1]) == []
    )  # what was stored is notes; the digest of the new turn is what carries pictures
    assert any("cat.png" in m.text for m in later[:-1])  # still known by name


async def test_what_a_voice_note_says_is_in_the_digest() -> None:
    scout = Actor("member", "scout@g")
    model = ScriptedModel(PASS)
    note = {
        "name": "note.webm",
        "size": 9,
        "mime": "audio/webm",
        "key": "k/n",
        "transcript": "meet at noon",
    }
    async with ephemeral_runtime() as rt:
        await rt.register(member("scout", model, roster(scout)))
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(
            G, sender=HUMAN, text="", data={"attachments": [note]}
        )
        await settle(rt.store)
    assert "said: meet at noon" in last_text(model.seen[0])


async def test_a_member_shares_a_file_it_made_with_the_attach_tool() -> None:
    scout = Actor("member", "scout@g")
    made = {"name": "chart.png", "size": 3, "mime": "image/png", "key": "k/chart.png"}
    seen: list[str] = []

    async def publish(path: str) -> dict:
        seen.append(path)
        return made

    model = ScriptedModel(
        ToolCall("attach", {"path": "/groups/trip/chart.png"}), "Here is the chart."
    )
    async with ephemeral_runtime() as rt:
        await rt.register(member_with("scout", model, roster(scout), publish=publish))
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(G, sender=HUMAN, text="chart it")
        await settle(rt.store)
        entries = await rt.store.channel_read(G)
    assert seen == ["/groups/trip/chart.png"]
    reply = entries[-1]
    assert reply.text == "Here is the chart." and reply.data["attachments"] == [made]


async def test_a_file_that_cannot_be_shared_is_reported_to_the_member() -> None:
    scout = Actor("member", "scout@g")

    async def publish(path: str) -> dict:
        raise ValueError("there is no such file")

    model = ScriptedModel(
        ToolCall("attach", {"path": "/workspace/missing.png"}), "Sorry, that failed."
    )
    async with ephemeral_runtime() as rt:
        await rt.register(member_with("scout", model, roster(scout), publish=publish))
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(G, sender=HUMAN, text="chart it")
        await settle(rt.store)
        entries = await rt.store.channel_read(G)
    told = [
        b.text
        for m in model.seen[-1]
        for r in m.content
        if isinstance(r, ToolResultBlock)
        for b in r.content
        if isinstance(b, TextBlock)
    ]
    assert any("no such file" in t for t in told)
    assert (
        entries[-1].text == "Sorry, that failed."
        and "attachments" not in entries[-1].data
    )


# -- attention: a cheap look before a full turn, following a conversation, being busy ---------------------------------------------------


def attending(
    name: str,
    main: ScriptedModel,
    names: dict[str, str],
    triage: ScriptedModel | None = None,
    **config,
) -> ChannelMemberAgent:
    return member_with(name, main, names, triage=triage, **config)


async def test_an_ambient_message_gets_a_cheap_look_and_a_pass_costs_no_full_turn() -> (
    None
):
    scout = Actor("member", "scout@g")
    main, triage = ScriptedModel("never used"), ScriptedModel("PASS")
    async with ephemeral_runtime() as rt:
        await rt.register(attending("scout", main, roster(scout), triage))
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(G, sender=HUMAN, text="anyone for lunch?")
        await settle(rt.store)
        entries = await rt.store.channel_read(G)
        (me,) = await rt.store.channel_members(G)
    assert main._calls == 0 and triage._calls == 1
    assert [e.text for e in entries] == [
        "anyone for lunch?"
    ] and me.cursor == 0  # looked at, judged not for it


async def test_a_member_that_decides_to_speak_gets_a_full_turn_with_what_came_before() -> (
    None
):
    scout = Actor("member", "scout@g")
    main, triage = ScriptedModel("Ramen, I would say."), ScriptedModel("PASS", "SPEAK")
    async with ephemeral_runtime() as rt:
        await rt.register(attending("scout", main, roster(scout), triage))
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(G, sender=HUMAN, text="I am hungry.")
        await settle(rt.store)
        await rt.store.channel_append(G, sender=HUMAN, text="what should we eat?")
        await settle(rt.store)
        entries = await rt.store.channel_read(G)
    assert [e.text for e in entries][-1] == "Ramen, I would say."
    seen = last_text(main.seen[0])
    assert (
        "(earlier, already seen)" in seen
        and "I am hungry." in seen
        and "what should we eat?" in seen
    )


async def test_a_message_for_the_member_is_answered_without_the_cheap_look() -> None:
    scout = Actor("member", "scout@g")
    main, triage = ScriptedModel("Tokyo."), ScriptedModel("PASS")
    async with ephemeral_runtime() as rt:
        await rt.register(attending("scout", main, roster(scout), triage))
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(
            G, sender=HUMAN, text="@Scout capital of Japan?", mentions=[str(scout)]
        )
        await settle(rt.store)
        entries = await rt.store.channel_read(G)
    assert triage._calls == 0 and entries[-1].text == "Tokyo."


async def test_a_member_drawn_into_a_conversation_follows_it_without_being_named_again() -> (
    None
):
    scout = Actor("member", "scout@g")
    main, triage = ScriptedModel("Tokyo.", "Seoul."), ScriptedModel("SPEAK")
    async with ephemeral_runtime() as rt:
        await rt.register(attending("scout", main, roster(scout), triage))
        await rt.store.channel_open(
            G, members=[Member(agent=scout, mode=Mode.MENTIONS)]
        )
        await rt.store.channel_append(
            G, sender=HUMAN, text="@Scout capital of Japan?", mentions=[str(scout)]
        )
        await settle(rt.store)
        await rt.store.channel_append(
            G, sender=HUMAN, text="and of Korea?"
        )  # nobody is named
        await settle(rt.store)
        entries = await rt.store.channel_read(G)
    assert [e.text for e in entries if e.sender == scout] == ["Tokyo.", "Seoul."]
    assert triage._calls == 1  # the first was for it; the follow-up got a cheap look


async def test_a_busy_member_leaves_chatter_unread_but_still_answers_when_named() -> (
    None
):
    scout = Actor("member", "scout@g")
    main, triage = ScriptedModel("On it."), ScriptedModel("SPEAK")

    async def busy() -> bool:
        return False

    async with ephemeral_runtime() as rt:
        await rt.register(
            attending("scout", main, roster(scout), triage, availability=busy)
        )
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(G, sender=HUMAN, text="anyone for lunch?")
        await settle(rt.store)
        (me,) = await rt.store.channel_members(G)
        assert (
            main._calls == 0 and triage._calls == 0 and me.cursor == -1
        )  # left for later, not lost
        await rt.store.channel_append(
            G, sender=HUMAN, text="@Scout please", mentions=[str(scout)]
        )
        await settle(rt.store)
        entries = await rt.store.channel_read(G)
    assert entries[-1].text == "On it."
    assert "anyone for lunch?" in last_text(
        main.seen[0]
    )  # what it missed is there when it does look


async def test_the_cheap_look_knows_who_everyone_is_and_what_each_does() -> None:
    scout, quill = Actor("member", "scout@g"), Actor("member", "quill@g")
    names = roster(scout, quill)
    triage = ScriptedModel("PASS")
    async with ephemeral_runtime() as rt:
        await rt.register(
            attending(
                "scout",
                ScriptedModel("x"),
                names,
                triage,
                roles={"Scout": "Researcher", "Quill": "Editor"},
            )
        )
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(G, sender=HUMAN, text="who edits?")
        await settle(rt.store)
    shown = last_text(triage.seen[0])
    assert (
        "Quill (Editor)" in shown
        and "Scout (Researcher)" in shown
        and "who edits?" in shown
    )


async def test_the_cheap_look_sees_that_a_picture_was_shared_and_is_told_a_question_to_the_group_is_worth_answering() -> (
    None
):
    scout = Actor("member", "scout@g")
    triage = ScriptedModel("PASS")
    async with ephemeral_runtime() as rt:
        await rt.register(attending("scout", ScriptedModel("x"), roster(scout), triage))
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(
            G,
            sender=HUMAN,
            text="what is this place?",
            data={"attachments": [picture("temple.png")]},
        )
        await settle(rt.store)
    shown = last_text(triage.seen[0])
    assert "temple.png" in shown  # not just the words: there is a picture
    assert "SPEAK if a new message asks a question" in shown


async def test_what_a_member_decided_not_to_say_is_not_kept_as_if_it_were_said() -> (
    None
):
    """Only turns that were posted are history: a [PASS], or an answer that went stale, would otherwise sit in every later prompt."""
    scout = Actor("member", "scout@g")
    main = ScriptedModel(PASS, "Four.")
    async with ephemeral_runtime() as rt:
        await rt.register(member_with("scout", main, roster(scout)))
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(G, sender=HUMAN, text="hello there")
        await settle(rt.store)
        await rt.store.channel_append(G, sender=HUMAN, text="2+2?")
        await settle(rt.store)
    later = main.seen[-1]
    assert not any(PASS in m.text for m in later[:-1])
    assert not any(
        "hello there" in m.text for m in later[:-1]
    )  # only the new digest (which shows it as context) mentions it


async def test_the_cheap_look_is_shown_what_was_said_just_before_so_that_it_can_follow_what_that_refers_to() -> (
    None
):
    """ "whats that place?" means nothing without the picture shared a moment earlier, to a person or to a model."""
    scout = Actor("member", "scout@g")
    triage = ScriptedModel("PASS")
    async with ephemeral_runtime() as rt:
        await rt.register(attending("scout", ScriptedModel("x"), roster(scout), triage))
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(
            G,
            sender=HUMAN,
            text="look at this",
            data={"attachments": [picture("temple.png")]},
        )
        await settle(rt.store)
        await rt.store.channel_append(G, sender=HUMAN, text="whats that place?")
        await settle(rt.store)
    second = last_text(triage.seen[1])
    assert (
        "whats that place?" in second
        and "look at this" in second
        and "temple.png" in second
    )
    assert second.index("look at this") < second.index("whats that place?")


async def test_a_picture_shared_a_moment_before_is_still_in_front_of_the_model_when_it_is_asked_about_it() -> (
    None
):
    scout = Actor("member", "scout@g")
    model = seeing(ScriptedModel("A temple in Tokyo."))
    triage = ScriptedModel("PASS", "SPEAK")
    async with ephemeral_runtime() as rt:
        await rt.register(
            attending("scout", model, roster(scout), triage, media=loader)
        )
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(
            G,
            sender=HUMAN,
            text="look at this",
            data={"attachments": [picture("temple.png")]},
        )
        await settle(rt.store)
        await rt.store.channel_append(G, sender=HUMAN, text="whats that place?")
        await settle(rt.store)
    assert media_in(model.seen[0]) == ["temple.png"]


async def test_a_member_asked_directly_about_a_picture_shared_earlier_is_shown_it() -> (
    None
):
    scout = Actor("member", "scout@g")
    model = seeing(ScriptedModel("A temple."))
    async with ephemeral_runtime() as rt:
        await rt.register(
            attending(
                "scout", model, roster(scout), ScriptedModel("PASS"), media=loader
            )
        )
        await rt.store.channel_open(G, members=[Member(agent=scout)])
        await rt.store.channel_append(
            G,
            sender=HUMAN,
            text="look at this",
            data={"attachments": [picture("temple.png")]},
        )
        await settle(rt.store)  # it looked, and passed
        await rt.store.channel_append(
            G, sender=HUMAN, text="@Scout what place is that?", mentions=[str(scout)]
        )
        await settle(rt.store)
    assert media_in(model.seen[0]) == [
        "temple.png"
    ] and "(earlier, already seen)" in last_text(model.seen[0])


async def test_what_happened_to_an_entry_is_not_something_a_member_reads() -> None:
    """A reaction, an edit marker or a delete marker is for a person's screen: the member's digest has only what was said."""
    scout = Actor("member", "scout@g")
    names = roster(scout)
    seen: list[str] = []

    def reply(messages) -> str:
        seen.append(last_text(messages))
        return PASS

    async with ephemeral_runtime() as rt:
        await rt.register(member("scout", ScriptedModel(reply), names))
        store = rt.store
        await store.channel_open(G, members=[Member(agent=scout)])
        sent = await store.channel_append(G, sender=HUMAN, text="the plan is x")
        await settle(store)
        await store.channel_react(G, sent.seq, HUMAN, "👍")
        await store.channel_edit(G, sent.seq, HUMAN, "the plan is y")
        await store.channel_append(G, sender=HUMAN, text="and then z")
        await settle(store)
        members = {m.agent: m for m in await store.channel_members(G)}
        latest = (await store.channel_last(G, 1))[0].seq

    digests = "\n".join(seen)
    assert "👍" not in digests and "previous" not in digests and "and then z" in digests
    assert (
        members[scout].cursor == latest
    )  # it has read past the markers, so they never wake it again
