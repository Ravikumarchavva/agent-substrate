"""``/rag`` and ``/internal/knowledge``: a tenant's knowledge bases are its own, whatever the request says.

The routes used to take the collection name from the request body, so any authenticated caller could query, list and delete another tenant's
collections. Now the collection is always derived from the authenticated claims. These tests run the real routes against a real ``Library``
(a store in a temp folder) as two tenants.
"""

from __future__ import annotations

import io

import httpx
import pytest
from fastapi import FastAPI

from substrate.documents import Library, Reader
from substrate.stores import Store
from substrate_cloud.monolith.dependencies import ServerDependencies, get_ctx
from substrate_cloud.monolith.routes.knowledge import router as knowledge_router
from substrate_cloud.monolith.routes.rag import router as rag_router
from substrate_cloud.monolith.security.deps import get_current_user
from substrate_cloud.shared.auth.claims import AuthClaims

ACME = AuthClaims(sub="u-acme", tenant_id="acme")
EVIL = AuthClaims(sub="u-evil", tenant_id="evilcorp")
POLICY = b"Refund policy. Customers may return damaged goods within thirty days for a full refund. " * 20
ROTTERDAM = b"Shipping. Orders to Rotterdam leave the Dutch warehouse in two days. " * 20


@pytest.fixture
async def world(tmp_path):
    store = Store.at(tmp_path / "store")
    await store.start()
    knowledge = Library(store, reader=Reader(isolate=False))
    app = FastAPI()
    app.include_router(rag_router)
    app.include_router(knowledge_router)
    app.state.knowledge = knowledge
    app.dependency_overrides[get_ctx] = lambda: ServerDependencies(
        model_client=None,
        history=None,
        tools=None,
        bridge_registry=None,
        tools_requiring_approval=[],
        system_instructions="",
        tool_timeout=60.0,
        file_store=store.files,
        knowledge=knowledge,
    )
    state = {"who": ACME}
    app.dependency_overrides[get_current_user] = lambda: state["who"]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        yield http, state, knowledge, store
    await store.aclose()


async def test_a_tenant_adds_and_searches_its_own_knowledge_base(world):
    http, state, _knowledge, _store = world
    resp = await http.post("/rag/ingest", json={"content": POLICY.decode(), "knowledge_base": "hr", "filename": "policy.md"})
    assert resp.status_code == 200 and resp.json()["document_id"].startswith("policy-")
    found = await http.post("/rag/query", json={"question": "refund damaged goods", "knowledge_base": "hr"})
    body = found.json()
    assert found.status_code == 200 and body["results"] and "refund" in body["results"][0]["text"].lower()
    assert body["results"][0]["metadata"]["filename"] == "policy.md" and body["results"][0]["metadata"]["pages"][0] >= 1


async def test_another_tenant_cannot_query_list_or_delete_it(world):
    http, state, _knowledge, _store = world
    await http.post("/rag/ingest", json={"content": POLICY.decode(), "knowledge_base": "hr", "filename": "policy.md"})
    state["who"] = EVIL
    # the same knowledge base name, a different tenant: it is *their* (empty) knowledge base, not acme's
    assert (await http.post("/rag/query", json={"question": "refund", "knowledge_base": "hr"})).json()["results"] == []
    assert (await http.get("/rag/collections")).json()["collections"] == []
    assert (await http.delete("/rag/collections/hr")).json()["deleted"] == 0
    # a name that tries to be a path or another tenant's collection is refused or inert, never followed
    for hostile in ["../acme/hr", "acme/knowledge/hr/library", "tenants/acme/knowledge/hr/library"]:
        resp = await http.post("/rag/query", json={"question": "refund", "knowledge_base": hostile})
        assert resp.status_code == 422 or resp.json()["results"] == []
    state["who"] = ACME
    assert (await http.post("/rag/query", json={"question": "refund", "knowledge_base": "hr"})).json()["results"], "acme's was never touched"


async def test_collections_lists_only_the_callers_tenant_and_delete_removes_one(world):
    http, state, knowledge, _store = world
    await http.post("/rag/ingest", json={"content": POLICY.decode(), "knowledge_base": "hr", "filename": "a.md"})
    await http.post("/rag/ingest", json={"content": ROTTERDAM.decode(), "knowledge_base": "ops", "filename": "b.md"})
    state["who"] = EVIL
    await http.post("/rag/ingest", json={"content": POLICY.decode(), "knowledge_base": "secret", "filename": "c.md"})
    state["who"] = ACME
    listed = (await http.get("/rag/collections")).json()["collections"]
    assert listed == [{"name": "hr", "documents": 1}, {"name": "ops", "documents": 1}]
    assert (await http.delete("/rag/collections/hr")).json() == {"deleted": 1, "knowledge_base": "hr"}
    assert [c["name"] for c in (await http.get("/rag/collections")).json()["collections"]] == ["ops"]
    state["who"] = EVIL
    assert [c["name"] for c in (await http.get("/rag/collections")).json()["collections"]] == ["secret"]


async def test_an_unreadable_document_is_a_422_with_a_reason(world):
    http, _state, _knowledge, _store = world
    resp = await http.post("/rag/ingest", json={"content": "  ", "knowledge_base": "hr", "filename": "empty.txt"})
    assert resp.status_code == 422 and resp.json()["detail"]


async def test_the_knowledge_upload_keeps_the_original_and_files_the_document_under_the_tenant(world):
    http, state, knowledge, store = world
    files = {"file": ("handbook.md", io.BytesIO(b"# Handbook\n\n" + POLICY), "text/markdown")}
    resp = await http.post("/internal/knowledge/hr/documents", files=files)
    body = resp.json()
    assert resp.status_code == 200 and body["indexed"] is True and body["storage_key"].startswith("tenants/acme/knowledge/hr/documents/")
    assert await store.files.exists(body["storage_key"])
    found = await http.post("/rag/query", json={"question": "refund damaged goods", "knowledge_base": "hr"})
    assert found.json()["results"][0]["metadata"]["document_id"] == body["document_id"]
    state["who"] = EVIL
    assert (await http.post("/rag/query", json={"question": "refund", "knowledge_base": "hr"})).json()["results"] == []


async def test_an_upload_that_cannot_be_read_is_kept_and_reported_not_lost(world):
    http, _state, _knowledge, store = world
    files = {"file": ("blob.bin", io.BytesIO(bytes(range(256)) * 20), "application/octet-stream")}
    body = (await http.post("/internal/knowledge/hr/documents", files=files)).json()
    assert body["indexed"] is False and "does not look like a document" in body["error"] and await store.files.exists(body["storage_key"])


async def test_without_a_library_the_routes_say_so(world):
    http, _state, _knowledge, _store = world
    http._transport.app.state.knowledge = None
    assert (await http.post("/rag/query", json={"question": "q"})).status_code == 503
