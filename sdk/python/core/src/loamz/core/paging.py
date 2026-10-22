"""Paging: a list call returns a lazy pager that fetches pages as you iterate, so `for x in client.list_things()` works
on any size, and `.by_page()` exposes the pages (and their continuation tokens) when you need them.

    for thing in client.list_things():            # items, across pages, fetched on demand
        ...
    for page in client.list_things().by_page():   # one page (a list) at a time
        ...
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import TypeVar

T = TypeVar("T")

GetPage = Callable[[str | None], tuple[list[T], str | None]]
"""Fetch the page after `token` (None: the first) -> (its items, the next token or None)."""

AsyncGetPage = Callable[[str | None], Awaitable[tuple[list[T], str | None]]]


class ItemPaged[T]:
    def __init__(self, get_page: GetPage[T], *, token: str | None = None) -> None:
        self._get_page = get_page
        self._token = token

    def by_page(self, continuation_token: str | None = None) -> Iterator[list[T]]:
        token = continuation_token if continuation_token is not None else self._token
        while True:
            items, token = self._get_page(token)
            yield items
            if not token:
                return

    def __iter__(self) -> Iterator[T]:
        for page in self.by_page():
            yield from page


class AsyncItemPaged[T]:
    def __init__(self, get_page: AsyncGetPage[T], *, token: str | None = None) -> None:
        self._get_page = get_page
        self._token = token

    async def by_page(self, continuation_token: str | None = None) -> AsyncIterator[list[T]]:
        token = continuation_token if continuation_token is not None else self._token
        while True:
            items, token = await self._get_page(token)
            yield items
            if not token:
                return

    async def __aiter__(self) -> AsyncIterator[T]:
        async for page in self.by_page():
            for item in page:
                yield item
