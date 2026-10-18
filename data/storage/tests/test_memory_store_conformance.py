"""Memory and session state on both databases, held to their conformance suites."""

from __future__ import annotations

import pytest

from loamz.data.storage.testing.conformance.memory_store import MemoryStoreConformance
from loamz.data.storage.testing.conformance.short_term_memory import ShortTermMemoryConformance


class TestMemory(MemoryStoreConformance):
    @pytest.fixture
    async def store(self, storage):
        self.storage = storage
        return storage.memory

    async def residue(self, store, needle):
        """The rows (and the search column) must not hold the erased text."""
        async with self.storage.database.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT seq FROM memory_records WHERE text LIKE ? OR record_json LIKE ?",
                f"%{needle}%",
                f"%{needle}%",
            )
        return [f"memory_records row {r['seq']}" for r in rows]


class TestMemoryOnDisk(MemoryStoreConformance):
    """On a SQLite file, erasure must also leave no trace in the file or its write-ahead log."""

    @pytest.fixture
    async def store(self, tmp_path):
        from loamz.data.storage import Storage

        self.root = tmp_path / "store"
        storage = Storage.at(self.root)
        yield storage.memory
        await storage.aclose()

    async def residue(self, store, needle):
        return [
            str(p)
            for p in self.root.rglob("*")
            if p.is_file() and needle.encode() in p.read_bytes()
        ]


class TestSessionState(ShortTermMemoryConformance):
    @pytest.fixture
    async def store(self, storage):
        return storage.session_state
