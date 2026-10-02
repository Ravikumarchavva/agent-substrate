"""The kernel's history provider (local filesystem), held to the conformance suite."""

from __future__ import annotations

import pytest

from substrate.stores import LocalFilesystemHistoryProvider
from substrate.testing.conformance.history_provider import HistoryProviderConformance


class TestLocalFilesystemHistoryProvider(HistoryProviderConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return LocalFilesystemHistoryProvider(tmp_path)
