from datetime import UTC, datetime, timedelta

import pytest

from loamz.agents.features.people.usage.store import usage_by_day
from loamz.agents.kernel.runtime.store import NewEntry, RunSpec
from loamz.agents.kernel.types.identity import Actor
from loamz.agents.stores.headers import ThreadHeader
from loamz.agents.testing.app import serving
from loamz.agents.testing.runtime import runtime_store

TENANT = "acme"


@pytest.fixture
async def log():
    store = runtime_store()
    await store.start()
    yield store
    await store.aclose()


async def run(store, thread, entries, tenant=TENANT):
    r = await store.create_run(
        RunSpec(agent=Actor(type="agent", key="a"), tenant=tenant, thread_id=thread)
    )
    await store.annotate(r.run_id, [NewEntry(kind=k, payload=p) for k, p in entries])


def since(days: int) -> datetime:
    return (datetime.now(UTC) - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


async def test_a_days_messages_tokens_and_cost_are_summed(log):
    await run(
        log,
        "t1",
        [
            ("user.message", {"text": "hi"}),
            ("llm.call", {"tokens": 1000, "cost_usd": 0.002}),
            ("llm.call", {"tokens": 500, "cost_usd": 0.001}),
            ("user.message", {"text": "again"}),
        ],
    )
    series = await usage_by_day(log._owner, tenant=TENANT, thread_ids=["t1"], since=since(7))
    assert len(series) == 7  # no gaps in a chart
    today = series[-1]
    assert (today.messages, today.calls, today.tokens) == (2, 2, 1500)
    assert today.cost_usd == pytest.approx(0.003)
    assert all(d.messages == 0 and d.tokens == 0 for d in series[:-1])


async def test_only_the_given_threads_and_the_tenant_count(log):
    await run(log, "mine", [("llm.call", {"tokens": 100, "cost_usd": 0.1})])
    await run(log, "theirs", [("llm.call", {"tokens": 900, "cost_usd": 0.9})])
    await run(
        log, "other-tenant", [("llm.call", {"tokens": 900, "cost_usd": 0.9})], tenant="globex"
    )
    series = await usage_by_day(
        log._owner, tenant=TENANT, thread_ids=["mine", "other-tenant"], since=since(1)
    )
    assert series[-1].tokens == 100


async def test_no_threads_is_an_empty_week_not_an_error(log):
    series = await usage_by_day(log._owner, tenant=TENANT, thread_ids=[], since=since(3))
    assert [d.tokens for d in series] == [0, 0, 0]


async def test_a_malformed_entry_is_skipped_not_fatal(log):
    await run(log, "t1", [("llm.call", {"tokens": "lots", "cost_usd": None})])
    series = await usage_by_day(log._owner, tenant=TENANT, thread_ids=["t1"], since=since(1))
    assert series[-1].tokens == 0


async def test_a_store_that_never_ran_anything_has_no_usage(tmp_path):
    async with serving(tmp_path, only=("people.usage",)) as served:
        async with served.client("acme", "alice") as c:
            body = (await c.get("/api/v1/me/usage?days=3")).json()
            assert len(body["days"]) == 3 and body["window"]["tokens"] == 0
            assert (await c.get("/api/v1/me/usage?days=500")).status_code == 422


async def test_the_api_sums_only_the_callers_own_threads(tmp_path):
    async with serving(tmp_path, only=("people.usage",)) as served:
        store = served.container.agent_store
        await store.thread_headers.create(
            ThreadHeader(id="mine", tenant_id="acme", user_id="alice")
        )
        await store.thread_headers.create(ThreadHeader(id="his", tenant_id="acme", user_id="bob"))
        from loamz.agents.kernel.runtime.persistence.store import RuntimeStore

        runtime = RuntimeStore(served.container.database)
        await runtime.start()
        for thread, tokens in (("mine", 100), ("his", 900)):
            await run(
                runtime,
                thread,
                [("user.message", {}), ("llm.call", {"tokens": tokens, "cost_usd": 0.5})],
            )
        async with served.client("acme", "alice") as c:
            body = (await c.get("/api/v1/me/usage")).json()
        assert body["today"] == {"messages": 1, "tokens": 100, "cost_usd": 0.5}
        assert body["this_month"]["tokens"] == 100 and len(body["days"]) == 30
