"""Reading across conversations: message counts and search over the run log, and the Markdown/JSON export a conversation is kept as.
A real runtime store on a folder (SQLite); no server, no Postgres."""

from __future__ import annotations

import pytest

from substrate.runtime import NewEntry, RunSpec
from substrate.testing.runtime import runtime_store
from substrate.types import Actor
from substrate_cloud.stream.export import filename, messages_of, to_markdown
from substrate_cloud.stream.search import message_counts, search_messages, snippet

TENANT = "acme"


@pytest.fixture
async def log():
    store = runtime_store()
    await store.start()
    yield store
    await store.aclose()


async def say(store, thread: str, *lines: tuple[str, str], tenant: str = TENANT):
    run = await store.create_run(
        RunSpec(agent=Actor(type="agent", key="a"), tenant=tenant, thread_id=thread)
    )
    await store.annotate(
        run.run_id,
        [NewEntry(kind=kind, payload={"text": text}) for kind, text in lines],
    )


async def test_messages_are_counted_per_thread_and_only_for_the_tenant(log):
    await say(
        log,
        "t1",
        ("user.message", "hello"),
        ("assistant.message", "hi"),
        ("effect.result", "x"),
    )
    await say(log, "t2", ("user.message", "other"))
    await say(log, "t3", ("user.message", "not yours"), tenant="globex")
    counts = await message_counts(
        log._owner, tenant=TENANT, thread_ids=["t1", "t2", "t3", "none"]
    )
    assert counts == {"t1": 2, "t2": 1}


async def test_search_finds_words_in_messages_of_the_given_threads_only(log):
    await say(
        log,
        "t1",
        ("user.message", "What is the Rotterdam on-time rate?"),
        ("assistant.message", "On-time delivery was 94.2% in Rotterdam."),
    )
    await say(log, "t2", ("user.message", "Rotterdam in another thread"))
    hits = await search_messages(
        log._owner, tenant=TENANT, thread_ids=["t1"], query="rotterdam"
    )
    assert {h.thread_id for h in hits} == {"t1"}
    assert {h.role for h in hits} == {"user", "assistant"}
    assert all("otterdam" in h.snippet for h in hits)


async def test_search_matches_message_text_not_the_stored_json(log):
    await say(log, "t1", ("user.message", "hello there"))
    # "payload" and "kind" are in every stored entry; they are not words anyone said
    assert (
        await search_messages(
            log._owner, tenant=TENANT, thread_ids=["t1"], query="payload"
        )
        == []
    )
    assert (
        await search_messages(log._owner, tenant=TENANT, thread_ids=["t1"], query="a")
        == []
    )  # too short to be a search


async def test_percent_and_underscore_in_a_query_are_literal(log):
    await say(
        log,
        "t1",
        ("user.message", "growth was 50% in Q_3"),
        ("user.message", "growth was 500 in Q13"),
    )
    hits = await search_messages(
        log._owner, tenant=TENANT, thread_ids=["t1"], query="50%"
    )
    assert len(hits) == 1
    hits = await search_messages(
        log._owner, tenant=TENANT, thread_ids=["t1"], query="q_3"
    )
    assert len(hits) == 1


def test_a_snippet_shows_the_words_around_the_match_on_one_line():
    text = "word " * 40 + "the Rotterdam hub\nprocessed parcels " + "word " * 40
    out = snippet(text, "rotterdam")
    assert "Rotterdam hub processed" in out and "\n" not in out
    assert out.startswith("…") and out.endswith("…")


EVENTS = [
    {
        "type": "user.message",
        "text": "Summarise it",
        "attachments": [{"name": "q1.pdf"}],
    },
    {"type": "tool.call", "name": "documents"},
    {"type": "text.delta", "text": "Net sales "},
    {"type": "text.delta", "text": "rose 2%."},
    {"type": "user.message", "text": "Thanks", "attachments": []},
]


def test_an_export_has_what_was_said_and_nothing_else():
    messages = messages_of(EVENTS)
    assert [(m["role"], m["text"]) for m in messages] == [
        ("user", "Summarise it"),
        ("assistant", "Net sales rose 2%."),
        ("user", "Thanks"),
    ]
    md = to_markdown("Q1 results", messages)
    assert md.startswith("# Q1 results") and "*Attached: q1.pdf*" in md
    assert "documents" not in md  # tool calls are not exported


def test_download_names_are_safe():
    assert filename("Q1: results / 2024!", "md") == "q1-results-2024.md"
    assert filename("", "json") == "conversation.json"
    assert "/" not in filename("../../etc/passwd", "md")
