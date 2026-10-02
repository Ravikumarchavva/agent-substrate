"""The store's threads, held to the ``ThreadStore`` conformance suite."""

from __future__ import annotations

import pytest

from substrate.stores import Store
from substrate.testing.conformance.thread_store import ThreadStoreConformance


class TestThreads(ThreadStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        store = Store.at(tmp_path / "store")
        yield store.threads
        await store.aclose()
