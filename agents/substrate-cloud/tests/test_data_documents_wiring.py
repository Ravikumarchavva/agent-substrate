"""With ``DATA_SERVICE_URL`` set, documents and memory are the data service's (ADR 0004): the adapters are wired, the agent's own
database holds no corpus, and erasure goes data -> runtime -> redis. Without it nothing changes."""

from __future__ import annotations

import json
import re
import sqlite3
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from substrate.documents import Library
from substrate.documents.types import ExtractedPage, ExtractionResult
from substrate.integrations.data import (
    DataLibrary,
    DataMemoryStore,
    DataServiceConnection,
)
from substrate.stores import MemoryNamespace, Store
from substrate.stores.memory import MemoryQuery, MemoryRecord
from substrate_data_client.documents import collection_id

from substrate_cloud.config import SubstrateConfig
from substrate_cloud.data_access import DataDocumentAccess
from substrate_cloud.documents_library import build_knowledge_library, build_library
from substrate_cloud.gdpr.eraser import erase_tenant, erase_user

CORPUS = re.compile(
    r"^(vector_docs|vector_spaces|vector_fts.*|memory_records.*|library_.*|file_objects|files.*)$"
)


class FakeDataService:
    """Just enough of the data service's Documents and Memory routes to see which calls the platform makes."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.tokens: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v1")
        self.calls.append((request.method, path))
        self.tokens.append(request.headers.get("authorization", ""))
        body = json.loads(request.content) if request.content else {}
        if path == "/collections:ensure":
            return httpx.Response(
                201,
                json={
                    "id": collection_id(body["name"]),
                    "name": body["name"],
                    "semantic": body["semantic"],
                    "created": True,
                },
            )
        if path.endswith("/documents") and request.method == "POST":
            return httpx.Response(
                201,
                json={
                    "document": "d-1",
                    "title": "T",
                    "filename": body["filename"],
                    "pages": 1,
                    "sections": 1,
                    "images": 0,
                    "needs_ocr": [],
                    "duplicate": False,
                    "warnings": [],
                },
            )
        if path.endswith(":search"):
            return httpx.Response(200, json={"hits": [], "note": None})
        if path == "/memory/records:save":
            return httpx.Response(200, json={"id": body["id"]})
        if path == "/memory/records:query":
            return httpx.Response(200, json={"matches": []})
        return httpx.Response(
            404,
            json={"type": "https://substrate.dev/problems/not-found", "detail": "x"},
        )


def data_access(service: FakeDataService) -> DataDocumentAccess:
    async def token(tenant: str, user: str | None) -> str:
        return f"{tenant}/{user or '-'}"

    access = SimpleNamespace(
        connection=DataServiceConnection(
            "http://data", transport=httpx.MockTransport(service)
        ),
        document_token=token,
        memory_token=token,
    )
    return access  # type: ignore[return-value]


@pytest.fixture
def cfg() -> SubstrateConfig:
    return SubstrateConfig()


async def test_the_builders_return_data_adapters_only_with_a_data_service(
    cfg, tmp_path
):
    service = FakeDataService()
    data = data_access(service)
    library = build_library(None, None, cfg, data=data)
    knowledge = build_knowledge_library(None, None, cfg, data=data)
    assert isinstance(library, DataLibrary) and isinstance(knowledge, DataLibrary)
    assert library.file_topics is False and knowledge.file_topics is True
    assert library.document_id("a.pdf", "ab" * 32) == Library.document_id(
        "a.pdf", "ab" * 32
    )

    store = Store.at(tmp_path / "agent")
    await store.start()
    try:
        local = build_library(store, store.files, cfg)
        assert isinstance(local, Library)  # unset: the folder library, as before
    finally:
        await store.aclose()
    await data.connection.aclose()


async def test_with_a_data_service_the_agent_database_holds_no_corpus(cfg, tmp_path):
    """The invariant of ADR 0004: no vector_docs, memory_records, library_* or files tables in the agent's own store."""
    service = FakeDataService()
    data = data_access(service)
    agent_store = Store.at(tmp_path / "agent")
    await agent_store.start()
    try:
        library = build_library(agent_store, agent_store.files, cfg, data=data)
        knowledge = build_knowledge_library(
            agent_store, agent_store.files, cfg, data=data
        )
        memory = DataMemoryStore(data.connection, data.memory_token)
        conversation = "tenants/acme/users/u1/conversations/c1/documents"
        kb = "tenants/acme/knowledge/handbook/library"
        page = ExtractedPage(page_number=1, text="x " * 400, markdown="x " * 400)
        source = ExtractionResult(
            pages=[page], markdown="<!-- page 1 -->\n\n" + "x " * 400, engine="t"
        )
        await library.add(source, "a.md", collection=conversation)
        await knowledge.add(source, "b.md", collection=kb)
        await knowledge.find(collection=kb, query="x")
        await memory.save(
            MemoryRecord.from_text(
                "likes tea", namespace=MemoryNamespace(tenant_id="acme", user_id="u1")
            )
        )
        await memory.query(
            MemoryQuery(namespace=MemoryNamespace(tenant_id="acme", user_id="u1"))
        )
    finally:
        await agent_store.aclose()
        await data.connection.aclose()

    # the calls went to the data service, each as the identity its collection names (a knowledge base: the platform)
    assert ("POST", f"/collections/{collection_id(kb)}/documents") in service.calls
    assert ("POST", "/memory/records:save") in service.calls
    tables = {
        row[0]
        for row in sqlite3.connect(Path(tmp_path / "agent" / "substrate.db")).execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
        )
    }
    assert [t for t in tables if CORPUS.match(t)] == [], sorted(tables)


class Journal:
    """Records the order in which erasure reaches the places data lives."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def data_admin(self) -> SimpleNamespace:
        events = self.events

        async def request(subject=None, *, tenant_id=None, everything=False):
            events.append("data")
            return {
                "removed": {
                    "files": {"files": 3, "versions": 4, "objects": 2},
                    "upload_sessions": 1,
                    "libraries": {"collections": 1, "documents": 2},
                    "vectors": 5,
                    "memory": 6,
                }
            }

        return SimpleNamespace(erasure=SimpleNamespace(request=request))

    def runtime_store(self) -> SimpleNamespace:
        events = self.events

        async def erase(**_):
            events.append("runtime")
            return 7

        async def channel_delete(*_):
            return None

        return SimpleNamespace(erase=erase, channel_delete=channel_delete)

    def redis(self) -> SimpleNamespace:
        events = self.events

        async def scan_iter(match="*"):
            events.append("redis")
            return
            yield

        return SimpleNamespace(scan_iter=scan_iter)


class NoLocalStore:
    async def delete_prefix(self, prefix: str) -> int:
        raise AssertionError(
            "the platform must not erase files itself when the data service holds them"
        )


@pytest.mark.requires_postgres
async def test_erasure_goes_data_then_runtime_then_redis(cfg, database_url):
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from substrate_cloud.monolith.models import Thread

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    tenant, user = f"tenant-{uuid.uuid4()}", str(uuid.uuid4())
    try:
        async with factory() as db:
            db.add(Thread(id=uuid.uuid4(), user_identifier=user, tenant_id=tenant))
            await db.commit()
            journal = Journal()
            summary = await erase_user(
                db, store=NoLocalStore(), redis=journal.redis(), tenant_id=tenant, user_id=user, cfg=cfg,
                runtime_store=journal.runtime_store(), data_admin=journal.data_admin(),
            )  # fmt: skip
            assert journal.events == ["data", "runtime", "redis"]
            assert (
                summary.objects_deleted,
                summary.documents_deleted,
                summary.memories_deleted,
                summary.vectors_deleted,
            ) == (3, 2, 6, 5)
            assert summary.runs_deleted == 7

            db.add(Thread(id=uuid.uuid4(), user_identifier=user, tenant_id=tenant))
            await db.commit()
            journal = Journal()
            summary = await erase_tenant(
                db, store=NoLocalStore(), redis=journal.redis(), tenant_id=tenant, cfg=cfg,
                runtime_store=journal.runtime_store(), data_admin=journal.data_admin(),
            )  # fmt: skip
            assert journal.events == ["data", "runtime", "redis"]
            assert (
                summary.objects_deleted,
                summary.documents_deleted,
                summary.memories_deleted,
            ) == (3, 2, 6)
    finally:
        await engine.dispose()
