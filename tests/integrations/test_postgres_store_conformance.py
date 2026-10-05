"""Every store facet on PostgreSQL, held to the same conformance suites as the folder store.

Same assertions, same facet code: what differs is only the database underneath, which is exactly what the suites are
there to prove. Needs a running Postgres with pgvector (``make infra-up``); skipped when unreachable.
"""

from __future__ import annotations

import pytest

from substrate.testing.conformance.file_store import FileStoreConformance
from substrate.testing.conformance.graph_store import GraphStoreConformance
from substrate.testing.conformance.memory_store import MemoryStoreConformance
from substrate.runtime.persistence.store import DurableRuntimeStore
from substrate.testing.conformance.runtime_store import NOW, RuntimeStoreConformance
from substrate.testing.conformance.short_term_memory import ShortTermMemoryConformance
from substrate.testing.conformance.task_store import TaskStoreConformance
from substrate.testing.conformance.thread_store import ThreadStoreConformance
from substrate.testing.conformance.vector_store import SearchableVectorStoreConformance
from substrate.testing.conformance.workspace_store import WorkspaceStoreConformance
from substrate.workspace import Workspaces
from tests._postgres import schema_store

pytestmark = [pytest.mark.requires_postgres]


class TestPostgresThreads(ThreadStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        async for s in schema_store(tmp_path):
            yield s.threads


class TestPostgresMemory(MemoryStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        async for s in schema_store(tmp_path):
            self.database = s.database
            yield s.memory

    async def residue(self, store, needle):
        """What this database holds is rows: an erased record has to be gone from them (and from the search column)."""
        async with self.database.transaction() as tx:
            rows = await tx.fetchall(
                "SELECT seq FROM memory_records WHERE text LIKE ? OR record_json LIKE ?",
                f"%{needle}%",
                f"%{needle}%",
            )
        return [f"memory_records row {r['seq']}" for r in rows]


class TestPostgresSessionState(ShortTermMemoryConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        async for s in schema_store(tmp_path):
            yield s.session_state


class TestPostgresTasks(TaskStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        async for s in schema_store(tmp_path):
            yield s.tasks


class TestPostgresGraph(GraphStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        async for s in schema_store(tmp_path):
            yield s.graph


class TestPostgresVectors(SearchableVectorStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        async for s in schema_store(tmp_path):
            yield s.vectors


class TestPostgresFiles(FileStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        self.root = tmp_path
        async for s in schema_store(tmp_path):
            yield s.files


class TestPostgresWorkspaces(WorkspaceStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        async for s in schema_store(tmp_path):
            yield Workspaces(s)


class TestPostgresRuntimeStore(RuntimeStoreConformance):
    @pytest.fixture
    async def store(self, tmp_path):
        async for s in schema_store(tmp_path):
            runtime = DurableRuntimeStore(s.database, clock=lambda: NOW)
            await runtime.start()
            yield runtime
