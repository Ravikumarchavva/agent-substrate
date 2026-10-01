"""The local filesystem memory store, held to the shared conformance suite."""

from __future__ import annotations

import pytest

from substrate.kernel.storage.local_memory_store import LocalFilesystemMemoryStore
from substrate.kernel.testing.conformance.memory_store import MemoryStoreConformance


class TestLocalFilesystemMemoryStore(MemoryStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        self.root = tmp_path
        return LocalFilesystemMemoryStore(tmp_path)

    async def residue(self, store, needle):
        return [str(p) for p in self.root.rglob("*") if p.is_file() and needle.encode() in p.read_bytes()]
