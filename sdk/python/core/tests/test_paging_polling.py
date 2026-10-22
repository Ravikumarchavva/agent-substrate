"""Pagers fetch lazily; pollers wait until the operation is done."""

import asyncio

import pytest

from loamz.core import AsyncItemPaged, ItemPaged
from loamz.core.polling import DONE, RUNNING, AsyncLROPoller, LROPoller

PAGES = {None: ([1, 2], "a"), "a": ([3], "b"), "b": ([4, 5], None)}


def test_a_pager_yields_items_across_pages_and_fetches_on_demand():
    calls = []

    def get(token):
        calls.append(token)
        return PAGES[token]

    pager = ItemPaged(get)
    assert calls == []
    assert list(pager) == [1, 2, 3, 4, 5]
    assert calls == [None, "a", "b"]


def test_by_page_exposes_pages_and_resumes_from_a_token():
    pager = ItemPaged(lambda token: PAGES[token])
    assert list(pager.by_page()) == [[1, 2], [3], [4, 5]]
    assert list(pager.by_page("a")) == [[3], [4, 5]]


def test_the_async_pager_matches_the_sync_one():
    async def get(token):
        return PAGES[token]

    async def run():
        return [x async for x in AsyncItemPaged(get)], [
            p async for p in AsyncItemPaged(get).by_page("b")
        ]

    assert asyncio.run(run()) == ([1, 2, 3, 4, 5], [[4, 5]])


def test_a_poller_waits_until_done_then_returns_the_value():
    states = iter([(RUNNING, None), (RUNNING, None), (DONE, "table")])
    sleeps = []
    poller = LROPoller(lambda: next(states), interval=0.5, sleep=sleeps.append)
    assert poller.status() == RUNNING
    assert poller.result() == "table" and sleeps == [0.5] and poller.done()


def test_a_failed_operation_raises_its_own_error_and_a_timeout_is_explicit():
    def failing():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        LROPoller(failing, sleep=lambda s: None).result()
    with pytest.raises(TimeoutError):
        LROPoller(lambda: (RUNNING, None), sleep=lambda s: None).result(timeout=0)


def test_the_async_poller_matches_the_sync_one():
    states = iter([(RUNNING, None), (DONE, 7)])

    async def poll():
        return next(states)

    async def sleep(_):
        return None

    assert asyncio.run(AsyncLROPoller(poll, sleep=sleep).result()) == 7
