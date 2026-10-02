"""The store's memory and session state, held to their conformance suites."""

from __future__ import annotations

import pytest

from substrate.stores import Store
from substrate.testing.conformance.memory_store import MemoryStoreConformance
from substrate.testing.conformance.short_term_memory import ShortTermMemoryConformance


class TestMemory(MemoryStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        self.root = tmp_path / "store"
        store = Store.at(self.root)
        yield store.memory
        await store.aclose()

    async def residue(self, store, needle):
        """Every file of the store — database, write-ahead log, files, indexes — scanned for the erased bytes."""
        return [str(p) for p in self.root.rglob("*") if p.is_file() and needle.encode() in p.read_bytes()]


class TestSessionState(ShortTermMemoryConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        store = Store.at(tmp_path / "store")
        yield store.session_state
        await store.aclose()
