"""``usage_by_day``: messages, tokens and cost summed from the runs' journals, for the caller's own conversations only."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from substrate.runtime import NewEntry, RunSpec
from substrate.testing.runtime import runtime_store
from substrate.types import Actor
from substrate_cloud.stream.usage import usage_by_day

TENANT = "acme"


@pytest.fixture
async def log():
    store = runtime_store()
    await store.start()
    yield store
    await store.aclose()


async def run(store, thread: str, entries, tenant: str = TENANT):
    r = await store.create_run(
        RunSpec(agent=Actor(type="agent", key="a"), tenant=tenant, thread_id=thread)
    )
    await store.annotate(r.run_id, [NewEntry(kind=k, payload=p) for k, p in entries])


def since(days: int) -> datetime:
    return (datetime.now(timezone.utc) - timedelta(days=days - 1)).replace(
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
    series = await usage_by_day(
        log._owner, tenant=TENANT, thread_ids=["t1"], since=since(7)
    )
    assert len(series) == 7  # no gaps in a chart
    today = series[-1]
    assert (today.messages, today.calls, today.tokens) == (2, 2, 1500)
    assert today.cost_usd == pytest.approx(0.003)
    assert all(d.messages == 0 and d.tokens == 0 for d in series[:-1])


async def test_only_the_given_threads_and_the_tenant_count(log):
    await run(log, "mine", [("llm.call", {"tokens": 100, "cost_usd": 0.1})])
    await run(log, "theirs", [("llm.call", {"tokens": 900, "cost_usd": 0.9})])
    await run(
        log,
        "other-tenant",
        [("llm.call", {"tokens": 900, "cost_usd": 0.9})],
        tenant="globex",
    )
    series = await usage_by_day(
        log._owner, tenant=TENANT, thread_ids=["mine", "other-tenant"], since=since(1)
    )
    assert series[-1].tokens == 100


async def test_no_threads_is_an_empty_week_not_an_error(log):
    series = await usage_by_day(
        log._owner, tenant=TENANT, thread_ids=[], since=since(3)
    )
    assert [d.tokens for d in series] == [0, 0, 0]


async def test_a_malformed_entry_is_skipped_not_fatal(log):
    await run(log, "t1", [("llm.call", {"tokens": "lots", "cost_usd": None})])
    series = await usage_by_day(
        log._owner, tenant=TENANT, thread_ids=["t1"], since=since(1)
    )
    assert series[-1].tokens == 0
