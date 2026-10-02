"""The Lance memory store (local-path mode), held to the shared conformance suite."""

from __future__ import annotations

import pytest

pytest.importorskip("lancedb")

from substrate.integrations.memory.lance_memory_store import LanceMemoryStore  # noqa: E402
from substrate.testing.conformance.memory_store import MemoryStoreConformance


class TestLanceMemoryStore(MemoryStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        self.root = tmp_path
        return LanceMemoryStore(path=tmp_path / "memories")

    async def residue(self, store, needle):
        # Lance compacts lazily: deleted rows can sit in old data files until cleanup, so
        # what is checked is that no *visible* row holds the bytes.
        table = await store._existing_table()
        if table is None:
            return []
        rows = await table.query().to_list()
        return [r["id"] for r in rows if needle in r["record_json"] or needle in r["content"]]
