"""`Files` on both databases, held to the `FileStore` conformance suite."""

from __future__ import annotations

import pytest

from loamz.data.storage.testing.conformance.file_store import FileStoreConformance


class TestFiles(FileStoreConformance):
    @pytest.fixture
    async def store(self, storage):
        self.root = storage.root
        return storage.files
