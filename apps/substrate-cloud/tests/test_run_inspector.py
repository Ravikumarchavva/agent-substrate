"""The run inspector reads a conversation's runs from their journals, and ratings on answers are kept one per answer for the owner only."""

from __future__ import annotations

import uuid

import pytest

from substrate.runtime import NewEntry, RunSpec
from substrate.testing.runtime import runtime_store
from substrate.types import Actor
from substrate_cloud.stream.runs import inspect_thread, last_message

from test_scheduled_notifications import session


@pytest.fixture
async def log():
    store = runtime_store()
    await store.start()
    yield store
    await store.aclose()


async def test_a_run_shows_its_cost_tools_and_outcome(log):
    run = await log.create_run(
        RunSpec(agent=Actor(type="agent", key="a"), tenant="acme", thread_id="t1")
    )
    await log.annotate(
        run.run_id,
        [
            NewEntry(kind=k, payload=p)
            for k, p in [
                ("user.message", {"text": "weather in Paris?"}),
                ("tool.call", {"call_id": "c1", "tool_name": "web_search", "args": {"q": "Paris"}, "risk": "safe"}),
                ("tool.result", {"call_id": "c1", "tool_name": "web_search", "ok": True, "output": "x" * 1000}),
                ("tool.call", {"call_id": "c2", "tool_name": "send_email", "args": {}}),
                ("llm.call", {"model": "m1", "tokens": 700, "cost_usd": 0.002}),
                ("llm.call", {"model": "m1", "tokens": 300, "cost_usd": 0.001}),
            ]
        ],
    )  # fmt: skip
    [detail] = await inspect_thread(log, "t1")
    assert detail.user_message == "weather in Paris?"
    assert (detail.llm_calls, detail.tokens, detail.model) == (2, 1000, "m1")
    assert detail.cost_usd == pytest.approx(0.003)
    search, email = detail.tools
    assert search.ok is True and len(search.output) < 500  # cut, not copied whole
    assert email.ok is None  # never came back: the run stopped first


async def test_a_thread_with_no_runs_has_nothing_to_inspect(log):
    assert await inspect_thread(log, "nobody") == []


@pytest.mark.requires_postgres
async def test_ratings_are_one_per_answer_and_can_be_taken_back():
    async with session() as c:
        thread = (await c.post("/threads", json={"name": "t"})).json()["id"]
        answer = str(uuid.uuid4())
        body = {"for_id": answer, "thread_id": thread}
        assert (await c.post("/feedbacks", json={**body, "value": -1, "comment": "wrong city"})).status_code == 201
        assert (await c.post("/feedbacks", json={**body, "value": 1})).status_code == 201
        got = (await c.get(f"/threads/{thread}/feedback")).json()
        assert [(f["for_id"], f["value"]) for f in got] == [(answer, 1)]  # replaced, not stacked
        await c.post("/feedbacks", json={**body, "value": 0})
        assert (await c.get(f"/threads/{thread}/feedback")).json() == []


@pytest.mark.requires_postgres
async def test_you_cannot_rate_or_inspect_someone_elses_conversation():
    async with session() as c:
        stranger = str(uuid.uuid4())
        assert (await c.post("/feedbacks", json={"for_id": str(uuid.uuid4()), "thread_id": stranger, "value": 1})).status_code == 404
        assert (await c.get(f"/threads/{stranger}/feedback")).status_code == 404
        assert (await c.get(f"/threads/{stranger}/runs")).status_code == 404


async def test_the_last_thing_said_is_a_one_line_preview(log):
    run = await log.create_run(RunSpec(agent=Actor(type="agent", key="a"), tenant="acme", thread_id="t9"))
    await log.annotate(run.run_id, [NewEntry(kind=k, payload=p) for k, p in [
        ("user.message", {"text": "What is\nthe plan?"}),
        ("assistant.message", {"text": "First  we ship.\nThen we rest."}),
    ]])  # fmt: skip
    assert await last_message(log, "t9") == "First we ship. Then we rest."
    assert await last_message(log, "nobody") is None
    only_user = await log.create_run(RunSpec(agent=Actor(type="agent", key="a"), tenant="acme", thread_id="t10"))
    await log.annotate(only_user.run_id, [NewEntry(kind="user.message", payload={"text": "hello" * 60})])
    preview = await last_message(log, "t10")
    assert preview.endswith("…") and len(preview) <= 141
