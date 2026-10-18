"""The Documents domain over HTTP: collections, documents, search, enrichment, governance and audit.

Runs the real `Library` engine on SQLite and local files behind the real API (no Docker); the volume-backed bundle store is
covered by the integration test in test_documents_volume.py.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from substrate.documents.types import (
    ExtractedImage,
    ExtractedImageLabel,
    ExtractedPage,
    ExtractionResult,
)
from substrate.testing.conformance.document_library import png, prose
from substrate_data_client import Forbidden, InsufficientScope, NotFound

T1, T2 = "acme", "globex"


def extraction(*pages: str, title: str | None = None, images=()) -> dict:
    result = ExtractionResult(
        pages=[
            ExtractedPage(page_number=n, text=t, markdown=t, images=list(images) if n == 1 else [])
            for n, t in enumerate(pages, start=1)
        ],
        markdown="\n\n".join(f"<!-- page {n} -->\n\n{t}" for n, t in enumerate(pages, start=1)),
        engine="test",
        title=title,
    )
    return json.loads(result.model_dump_json())


@pytest.fixture
def alice(docs):
    with docs.client("alice", T1) as client:
        yield client


def mine(user: str, kind: str = "documents", tenant: str = T1) -> str:
    return f"tenants/{tenant}/users/{user}/{kind}/{hashlib.md5(user.encode()).hexdigest()[:6]}"


def test_a_collection_is_ensured_once_and_documents_round_trip(alice):
    name = "tenants/acme/users/alice/conversations/c1/documents"
    first = alice.documents.ensure(name)
    assert alice.documents.ensure(name)["id"] == first["id"]
    cid = first["id"]
    added = alice.documents.add(
        cid,
        "refunds.pdf",
        extraction=extraction(prose("refund"), prose("warranty"), title="Refunds"),
    )
    assert added["pages"] == 2 and not added["duplicate"]
    again = alice.documents.add(
        cid,
        "refunds.pdf",
        extraction=extraction(prose("refund"), prose("warranty"), title="Refunds"),
    )
    assert again["duplicate"] is True
    doc = added["document"]

    listing = alice.documents.list(cid)
    assert [d["document"] for d in listing["documents"]] == [doc]
    assert alice.documents.get(cid, doc)["title"] == "Refunds"
    outline = alice.documents.outline(cid, doc)
    assert outline["sections"] and outline["document"]["document"] == doc
    passage = alice.documents.read(cid, doc, section=1)
    assert "refund" in passage["text"]
    assert alice.documents.section(cid, doc, 1)["text"] == passage["text"]
    assert "warranty" in alice.documents.read(cid, doc, pages=(2, 2))["text"]

    found = alice.documents.search(cid, "warranty committee")
    assert found["hits"] and found["hits"][0]["document"]["document"] == doc
    assert found["note"] is None
    assert alice.documents.reindex(cid) == 1

    assert any(c["name"] == name and c["documents"] == 1 for c in alice.documents.collections())
    alice.documents.delete(cid, doc)
    with pytest.raises(NotFound):
        alice.documents.get(cid, doc)
    assert alice.documents.search(cid, "warranty")["hits"] == []


def test_a_figure_can_be_viewed(alice):
    cid = alice.documents.ensure(mine("alice"))["id"]
    picture = ExtractedImage(
        data=png(),
        label=ExtractedImageLabel.CHART,
        page_number=1,
        caption="Quarterly sales",
        id="fig1",
    )
    added = alice.documents.add(
        cid, "sales.pdf", extraction=extraction(prose("sales"), images=[picture])
    )
    assert added["images"] == 1
    image = alice.documents.image(cid, document=added["document"], page=1)
    assert image.data == png() or image.data[:4] == b"\x89PNG"
    assert image.caption == "Quarterly sales" and image.label == "chart"


def test_uploads_are_read_by_the_document_reader_not_the_control_plane(docs, alice):
    cid = alice.documents.ensure(mine("alice", "raw"))["id"]
    added = alice.documents.add(
        cid, "notes.txt", data=(prose("lighthouse") + "\n").encode(), content_type="text/plain"
    )
    assert added["sections"] >= 1
    assert alice.documents.search(cid, "lighthouse")["hits"]
    # no reader configured: the control plane refuses to parse
    engine = docs.app.state.documents.engine
    inline, engine.can_read = engine.can_read, False
    try:
        with pytest.raises(Exception) as refused:
            alice.documents.add(cid, "more.txt", data=b"hello")
        assert getattr(refused.value, "status", None) == 503 or "503" in str(refused.value)
    finally:
        engine.can_read = inline


def test_collections_are_invisible_across_users_and_tenants(docs, alice):
    cid = alice.documents.ensure(mine("alice", "private"))["id"]
    alice.documents.add(cid, "secret.txt", extraction=extraction(prose("zeppelin")))
    with docs.client("bob", T1) as bob:
        with pytest.raises(Forbidden):  # another user's folder
            bob.documents.ensure(mine("alice", "private"))
        with pytest.raises(Forbidden):
            bob.documents.search(cid, "zeppelin")
        assert all("alice" not in c["name"] for c in bob.documents.collections())
    with docs.client("mallory", T2) as mallory:
        with pytest.raises(NotFound):  # a collection of another tenant does not exist for them
            mallory.documents.search(cid, "zeppelin")
        with pytest.raises(NotFound):
            mallory.documents.ensure(mine("alice", "private"))


def test_scopes_gate_the_routes(docs):
    with docs.client("alice", T1, "documents:read") as reader:
        cid = None
        with pytest.raises((Forbidden, InsufficientScope)):
            reader.documents.ensure(mine("alice", "scoped"))
    with docs.client("alice", T1, "files:write") as other:
        with pytest.raises(InsufficientScope):
            other.documents.collections()
    with docs.client("alice", T1, "documents:write") as writer:
        cid = writer.documents.ensure(mine("alice", "scoped"))["id"]
    with docs.client("alice", T1, "documents:read") as reader:
        assert reader.documents.list(cid)["documents"] == []
        with pytest.raises(InsufficientScope):
            reader.documents.add(cid, "x.txt", extraction=extraction("hello world " * 50))


def test_enrichment_is_written_back_by_the_caller(alice):
    cid = alice.documents.ensure(mine("alice", "enrich"))["id"]
    added = alice.documents.add(
        cid, "plan.pdf", extraction=extraction(prose("roadmap"), prose("budget"), title="Plan")
    )
    doc, enricher = added["document"], {"name": "test-model", "version": "1"}
    assert alice.documents.pending(cid, enricher=enricher) == [doc]
    brief = alice.documents.brief(cid, doc)
    assert [s["position"] for s in brief["sections"]] == [1, 2] or len(brief["sections"]) >= 1
    alice.documents.claim(cid, doc, enricher, 1)
    assert alice.documents.enrichment_state(cid, doc)["state"] == "running"
    alice.documents.annotate(
        cid, doc, card="The roadmap and the budget.", sections={1: "About the roadmap."},
        topics=["planning/roadmap"], enricher=enricher, attempts=1,
        usage={"input_tokens": 10, "output_tokens": 5},
    )  # fmt: skip
    assert alice.documents.enrichment_state(cid, doc)["state"] == "done"
    assert alice.documents.pending(cid, enricher=enricher) == []
    info = alice.documents.get(cid, doc)
    assert info["description"] == "The roadmap and the budget." and info["topics"] == [
        "planning/roadmap"
    ]
    assert "planning" in [c[0] for c in alice.documents.topics(cid)["children"]]
    assert "planning/roadmap" in alice.documents.topic_tree(cid)
    alice.documents.fail(cid, doc, {"name": "other", "version": "1"}, 2, "boom")
    assert alice.documents.enrichment_state(cid, doc)["state"] == "failed"
    alice.documents.set_topics(cid, {doc: ["ops/finance"]}, "tester")
    assert alice.documents.get(cid, doc)["topics"] == ["ops/finance"]
    assert alice.documents.pending_all(f"tenants/{T1}/users/alice/%", enricher=enricher)
    with pytest.raises(Exception):  # a pattern outside the tenant is refused
        alice.documents.pending_all("tenants/%", enricher=enricher)


def test_the_audit_log_holds_ids_only_and_still_verifies(docs, alice):
    name = mine("alice", "audited")
    cid = alice.documents.ensure(name)["id"]
    added = alice.documents.add(
        cid, "confidential-report-name.pdf", extraction=extraction(prose("falcon"))
    )
    alice.documents.delete(cid, added["document"])
    store = docs.app.state.services.store
    events = store.audit_events(None, 200)
    dump = json.dumps(events, default=str)
    for secret in ("confidential-report-name", "falcon", name, added["document"]):
        assert secret not in dump
    assert any(e["action"] == "document.add" for e in events)
    assert store.verify_audit()["ok"]


def test_semantic_search_fails_soft_to_words(docs, tmp_path_factory, identity, ticket_keys):
    """An embedding service that is down never fails an upload or a search: words answer, and the note says so."""
    from conftest import serve_documents

    class Down:
        model, dimensions, max_input_tokens = "down", None, 512

        from substrate.models.protocols import Modality

        modalities = frozenset({Modality.TEXT})

        async def embed(self, inputs, *, query=False):
            from substrate.types.errors import ServiceUnavailableError

            raise ServiceUnavailableError("embedder is down")

    service = serve_documents(
        identity, ticket_keys, tmp_path_factory.mktemp("soft"), embedder=Down()
    )
    try:
        with service.client("alice", T1) as client:
            cid = client.documents.ensure(mine("alice", "kb"), semantic=True)["id"]
            added = client.documents.add(cid, "kb.pdf", extraction=extraction(prose("harbor")))
            assert any("embedded" in w for w in added["warnings"])
            found = client.documents.search(cid, "harbor committee")
            assert found["hits"] and found["note"]
    finally:
        service.served.stop()


def test_semantic_collections_find_by_meaning_and_reindex_rebuilds_the_vectors(alice):
    cid = alice.documents.ensure(mine("alice", "sem"), semantic=True)["id"]
    refunds = "Customers may return damaged goods within thirty days for a full refund. " * 12
    shipping = "Orders to Rotterdam ship from the Dutch warehouse in two days by carrier. " * 12
    alice.documents.add(
        cid, "handbook.pdf",
        extraction=extraction(f"# Handbook\n\n## Refunds\n\n{refunds}", f"## Shipping\n\n{shipping}"),
    )  # fmt: skip
    found = alice.documents.search(cid, "return damaged goods refund")
    assert found["note"] is None and "refund" in found["hits"][0]["snippet"].lower()
    assert (
        "rotterdam"
        in alice.documents.search(cid, "Rotterdam warehouse")["hits"][0]["snippet"].lower()
    )
    assert alice.documents.reindex(cid) == 1
    assert alice.documents.search(cid, "Rotterdam warehouse")["hits"]
    assert alice.documents.embed_missing(cid) == 0


def test_collection_ids_are_the_published_contract(alice):
    from substrate_data_client.documents import collection_id

    name = mine("alice", "ids")
    assert alice.documents.ensure(name)["id"] == collection_id(name)
