"""The store's task boards, held to the ``TaskStore`` conformance suite."""

from __future__ import annotations

import pytest

from substrate.stores import Store
from substrate.testing.conformance.task_store import TaskStoreConformance


class TestTasks(TaskStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        store = Store.at(tmp_path / "store")
        yield store.tasks
        await store.aclose()
