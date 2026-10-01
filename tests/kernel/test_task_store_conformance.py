"""The kernel's local filesystem task store, held to the conformance suite."""

from __future__ import annotations

import pytest

from substrate.kernel.storage.local_tasks import LocalFilesystemTaskStore
from substrate.kernel.testing.conformance.task_store import TaskStoreConformance


class TestLocalFilesystemTaskStore(TaskStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        return LocalFilesystemTaskStore(tmp_path)
