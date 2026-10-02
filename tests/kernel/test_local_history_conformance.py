"""The kernel's history provider (local filesystem), held to the conformance suite."""

from __future__ import annotations

import pytest

from substrate.stores import LocalFilesystemThreadStore
from substrate.testing.conformance.thread_store import ThreadStoreConformance


class TestLocalFilesystemThreadStore(ThreadStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return LocalFilesystemThreadStore(tmp_path)
