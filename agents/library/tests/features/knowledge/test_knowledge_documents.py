import json

import pytest

from loamz.agents.bootstrap.settings import DocumentSettings
from loamz.agents.features.knowledge.documents.service import (
    DocumentsService,
    knowledge_collection_for,
)
from loamz.agents.features.knowledge.documents.store import LocalLibrary
from loamz.agents.features.knowledge.enrichment.service import EnrichmentService
from loamz.agents.kernel.caller import Caller
from loamz.agents.kernel.types.run import RunScope
from loamz.agents.providers.limits.memory import MemoryCounters
from loamz.agents.testing.app import serving
from loamz.agents.testing.scripted import ScriptedModel
from loamz.data.storage.errors import InvalidRequestError, QuotaExceededError

ALICE = Caller(tenant_id="acme", user_id="alice")
REPORT = b"# Q3 Report\n\nRevenue grew in Europe.\n\n## Costs\n\nCosts fell because of the hosting migration.\n"


async def test_library_files_finds_reads_and_isolates_tenants(db):
    library = LocalLibrary(db)
    acme = knowledge_collection_for("acme", "default")
    globex = knowledge_collection_for("globex", "default")
    added = await library.add(REPORT, "q3.md", collection=acme)
    assert added.sections == 2 and not added.duplicate
    assert (await library.add(REPORT, "q3.md", collection=acme)).duplicate
    hits = await library.find(collection=acme, query="hosting migration")
    assert hits and hits[0].section.title == "Costs"
    assert not await library.find(collection=globex, query="hosting")
    passage = await library.read(collection=acme, document=added.document, section=1)
    assert passage and "Revenue" in passage.text
    assert await library.collections(under="tenants/acme/knowledge/") == [(acme, 1)]
    assert await library.erase("acme") == 1
    assert not await library.find(collection=acme, query="hosting")


async def test_a_file_that_is_not_text_is_refused(db):
    with pytest.raises(InvalidRequestError):
        await LocalLibrary(db).add(
            b"\x00\x01", "x.bin", collection=knowledge_collection_for("acme", "kb")
        )


async def test_daily_document_limits_use_the_counters(db):
    docs = DocumentsService(LocalLibrary(db), MemoryCounters(), DocumentSettings(daily_doc_limit=2))
    await docs.claim_documents(ALICE, 2)
    with pytest.raises(QuotaExceededError):
        await docs.claim_documents(ALICE)
    assert (await docs.quota_status(ALICE))["used"] == 2
    await docs.release_documents(ALICE)
    await docs.claim_documents(ALICE)  # a released unit is free again
    await docs.claim_documents(Caller(tenant_id="acme", user_id="bob"))  # per user


async def test_the_documents_tool_reads_only_its_run_scope(db):
    docs = DocumentsService(LocalLibrary(db), MemoryCounters(), DocumentSettings())
    await docs.add_to_conversation(ALICE, "t1", REPORT, "q3.md")
    tool = docs.conversation_tool()

    class Ctx:
        scope = RunScope(tenant_id="acme", user_id="alice", thread_id="t1")

    found = await tool.execute(ctx=Ctx(), action="find", query="revenue")
    assert not found.is_error and "Revenue" in found.text and found.structured_content["citations"]
    other = type("C", (), {"scope": RunScope(tenant_id="acme", user_id="bob", thread_id="t1")})()
    assert (await tool.execute(ctx=other, action="list")).text == "No documents."


async def test_enrichment_describes_within_the_daily_token_cap(db):
    docs = DocumentsService(LocalLibrary(db), MemoryCounters(), DocumentSettings())
    reply = json.dumps(
        {
            "card": "A quarterly report.",
            "topics": ["Finance/Earnings"],
            "items": [{"position": 1, "description": "Revenue."}],
        }
    )
    cfg = DocumentSettings(summary_daily_tokens=0)
    service = EnrichmentService(docs, MemoryCounters(), cfg, lambda: ScriptedModel(reply))
    collection = knowledge_collection_for("acme", "default")
    added = await docs.library.add(REPORT, "q3.md", collection=collection)
    assert await service.process_pending("acme") == 1
    info = await docs.library.info(collection, added.document)
    assert info.description == "A quarterly report." and info.topics == ("Finance/Earnings",)
    assert info.enrichment == "done"
    assert await service.process_pending("acme") == 0
    browsed = await docs.library.browse(collection=collection, topic="Finance/Earnings")
    assert browsed and browsed.documents


async def test_a_tenant_past_its_daily_token_cap_is_not_described(db):
    docs = DocumentsService(LocalLibrary(db), MemoryCounters(), DocumentSettings())
    counters = MemoryCounters()
    await counters.add_day("enrich:tokens:acme", limit=10**9, count=500)
    service = EnrichmentService(
        docs, counters, DocumentSettings(summary_daily_tokens=500), lambda: ScriptedModel("{}")
    )
    await docs.library.add(REPORT, "q3.md", collection=knowledge_collection_for("acme", "default"))
    assert await service.process_pending("acme") == 0


async def test_routes_keep_knowledge_bases_per_tenant(tmp_path):
    async with serving(
        tmp_path, only=("knowledge.",), documents={"summary_enabled": False}
    ) as served:
        alice = served.client("acme", "alice")
        bob = served.client("globex", "bob")
        done = await alice.post(
            "/api/v1/rag/ingest", json={"content": REPORT.decode(), "filename": "q3.md"}
        )
        assert done.status_code == 200 and done.json()["sections"] == 2
        query = await alice.post("/api/v1/rag/query", json={"question": "hosting migration"})
        assert query.json()["results"][0]["metadata"]["section_title"] == "Costs"
        assert (await bob.post("/api/v1/rag/query", json={"question": "hosting"})).json()[
            "results"
        ] == []
        assert (await alice.get("/api/v1/rag/collections")).json() == {
            "items": [{"name": "default", "documents": 1}]
        }
        assert (await bob.get("/api/v1/rag/collections")).json() == {"items": []}
        assert (await bob.delete("/api/v1/rag/collections/default")).json()["deleted"] == 0
        assert (
            await alice.post("/api/v1/rag/ingest", json={"content": "x", "knowledge_base": "../x"})
        ).status_code == 400
        assert (await alice.delete("/api/v1/rag/collections/default")).json()["deleted"] == 1
        assert (await alice.get("/api/v1/rag/collections")).json() == {"items": []}


async def test_internal_upload_keeps_the_original_and_files_it(tmp_path):
    async with serving(
        tmp_path, only=("knowledge.",), documents={"summary_enabled": False}
    ) as served:
        tools = served.client("acme", "svc", "tools")
        up = await tools.post(
            "/api/v1/internal/knowledge/handbook/documents",
            files={"file": ("h.md", REPORT, "text/markdown")},
        )
        body = up.json()
        assert up.status_code == 200 and body["indexed"] and body["size_bytes"] == len(REPORT)
        assert await served.container.file_store.exists(body["storage_key"])
        bad = await tools.post(
            "/api/v1/internal/knowledge/handbook/documents",
            files={"file": ("h.bin", b"\x00", "application/pdf")},
        )
        assert bad.json()["indexed"] is False and "error" in bad.json()
        empty = await tools.post(
            "/api/v1/internal/knowledge/handbook/documents",
            files={"file": ("e.md", b"", "text/plain")},
        )
        assert empty.status_code == 400
