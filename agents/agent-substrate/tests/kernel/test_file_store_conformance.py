"""The store's files, held to the ``FileStore`` conformance suite."""

from __future__ import annotations

import pytest

from substrate.stores import Store
from substrate.testing.conformance.file_store import FileStoreConformance


class TestFiles(FileStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        self.root = tmp_path / "store"
        store = Store.at(self.root)
        yield store.files
        await store.aclose()
