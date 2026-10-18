"""Memory records, erasure and export over HTTP (no Docker): tenancy, ownership, receipts and the audit chain."""

from __future__ import annotations

import pytest
from substrate.testing.conformance.document_library import prose
from substrate_data_client import Forbidden, InsufficientScope, NotFound
from test_documents import extraction

A, B = "acme", "globex"


def record(text: str, id: str = "", **namespace) -> dict:
    return {
        "id": id,
        "content": [{"type": "text", "text": text}],
        "namespace": namespace,
        "category": "semantic",
    }


def texts(matches) -> list[str]:
    return sorted(m["record"]["content"][0]["text"] for m in matches)


def test_records_belong_to_their_user_and_tenant(docs):
    with (
        docs.client("alice", A) as alice,
        docs.client("bob", A) as bob,
        docs.client("eve", B) as eve,
    ):
        alice.memory.save(record("alice likes tea", "m-tea", user_id="alice"))
        assert alice.memory.get("m-tea", user_id="alice")["content"][0]["text"] == "alice likes tea"
        assert texts(alice.memory.query(user_id="alice", text_query="tea")) == ["alice likes tea"]
        # another user of the tenant, and another tenant, see nothing and cannot overwrite
        with pytest.raises(NotFound):
            bob.memory.get("m-tea", user_id="bob")
        assert bob.memory.query(user_id="bob", text_query="tea") == []
        with pytest.raises(NotFound):
            eve.memory.get("m-tea", user_id="eve")
        eve.memory.save(record("eve's own record with the same id", "m-tea", user_id="eve"))
        assert alice.memory.get("m-tea", user_id="alice")["content"][0]["text"] == "alice likes tea"
        # a token speaks only as its own user
        with pytest.raises(Forbidden):
            bob.memory.get("m-tea", user_id="alice")
        with pytest.raises(Forbidden):
            bob.memory.save(record("forged", "m-x", user_id="alice"))
        # taking over a record of another owner in the same tenant is refused
        with pytest.raises(Forbidden):
            bob.memory.save(record("takeover", "m-tea", user_id="bob"))
        alice.memory.touch(["m-tea"], user_id="alice")
        assert alice.memory.get("m-tea", user_id="alice")["access_count"] == 1
        alice.memory.delete("m-tea", user_id="alice")
        with pytest.raises(NotFound):
            alice.memory.get("m-tea", user_id="alice")


def test_tenant_level_memory_is_written_by_administrators_only(docs):
    with (
        docs.client("alice", A) as alice,
        docs.client("root", A, groups=["tenant-acme", "tenant-admins"]) as admin,
    ):
        with pytest.raises(Forbidden):
            alice.memory.save(record("closing time is five", "org-1"))
        admin.memory.save(record("closing time is five", "org-1"))
        assert texts(alice.memory.query(user_id="alice", text_query="closing")) == [
            "closing time is five"
        ]
        with pytest.raises(Forbidden):
            alice.memory.delete("org-1")
        with pytest.raises(Forbidden):  # reading across users is for administrators
            alice.memory.query(user_id="alice", tenant_wide_reason="curiosity")
        admin.memory.delete("org-1")


def test_memory_scopes_gate_the_routes(docs):
    with docs.client("alice", A, "memory:read") as reader:
        assert reader.memory.query(user_id="alice") is not None
        with pytest.raises(InsufficientScope):
            reader.memory.save(record("x", "m", user_id="alice"))
    with docs.client("alice", A, "documents:write") as other:
        with pytest.raises(InsufficientScope):
            other.memory.query(user_id="alice")


@pytest.fixture
def populated(docs):
    """Two users of one tenant and one of another, each with documents and memory."""
    made = {}
    for tenant, user in ((A, "erin"), (A, "frank"), (B, "erin")):
        with docs.client(user, tenant) as c:
            name = f"tenants/{tenant}/users/{user}/conversations/c1/documents"
            cid = c.documents.ensure(name, semantic=True)["id"]
            doc = c.documents.add(cid, "n.txt", extraction=extraction(prose(f"{user}{tenant}")))[
                "document"
            ]
            c.memory.save(
                record(f"{user} remembers {tenant}", f"mem-{tenant}-{user}", user_id=user)
            )
            made[tenant, user] = (cid, doc)
    return made


def test_erasure_removes_one_user_across_domains_and_leaves_the_audit_chain_intact(docs, populated):
    store = docs.app.state.services.store
    with docs.admin(A) as admin:
        receipt = admin.erasure.request("erin", tenant_id=A)
    assert (
        receipt["status"] == "complete" and receipt["scope"] == "user" and receipt["tenant_id"] == A
    )
    removed = receipt["removed"]
    assert removed["libraries"] == {"collections": 1, "documents": 1}
    assert removed["memory"] == 1 and removed["vectors"] >= 1  # the semantic collection had vectors
    assert (
        set(removed["files"]) == {"files", "versions", "objects"} and "upload_sessions" in removed
    )
    assert "erin" not in str(receipt)  # the receipt names the subject by digest only

    cid, doc = populated[A, "erin"]
    with docs.client("erin", A) as erin:
        with pytest.raises(NotFound):
            erin.documents.get(cid, doc)
        assert erin.memory.query(user_id="erin") == []
    # neighbours are untouched: another user of the tenant, and the same name in another tenant
    with docs.client("frank", A) as frank:
        fid, fdoc = populated[A, "frank"]
        assert frank.documents.get(fid, fdoc)["document"] == fdoc
        assert len(frank.memory.query(user_id="frank")) == 1
    with docs.client("erin", B) as erin_b:
        gid, gdoc = populated[B, "erin"]
        assert erin_b.documents.get(gid, gdoc)["document"] == gdoc
        assert len(erin_b.memory.query(user_id="erin")) == 1
    # idempotent, and the evidence still verifies
    with docs.admin(A) as admin:
        again = admin.erasure.request("erin", tenant_id=A)["removed"]
    assert again["libraries"]["documents"] == 0 and again["memory"] == 0
    assert store.verify_audit()["ok"]
    events = [e for e in store.audit_events(None, 500) if e["action"] == "erasure.complete"]
    assert events and "erin" not in str(events)


def test_erasure_authority(docs, populated):
    with docs.client("frank", A) as agent:  # a delegated token never reaches these routes
        with pytest.raises(InsufficientScope):
            agent.erasure.request("frank")
    with docs.admin(A) as person:  # the platform admin acts for the tenant it names
        with pytest.raises(Exception):  # but the subject form needs a subject
            person.erasure.request(tenant_id=A)
    from substrate_data_client import DataClient

    # an ordinary user (no delegation) may erase only themselves
    with DataClient(docs.url, token=lambda: docs.identity.token("erin", ())) as own:
        with pytest.raises(Forbidden):
            own.erasure.request("frank", tenant_id=A)


def test_export_hands_over_what_is_held_and_tenant_erasure_removes_everything(docs, populated):
    with docs.admin(B) as admin:
        bundle = admin.exports.create("erin", tenant_id=B)
        assert bundle["format"] == "substrate-export/v1" and bundle["subject"] == "erin"
        assert [c["name"] for c in bundle["collections"]] == [
            "tenants/globex/users/erin/conversations/c1/documents"
        ]
        assert len(bundle["collections"][0]["documents"]) == 1
        assert [m["namespace"]["user_id"] for m in bundle["memory"]] == ["erin"]
        whole = admin.exports.create(everything=True, tenant_id=B)
        assert len(whole["collections"]) == 1 and whole["scope"] == "tenant"
        receipt = admin.erasure.request(everything=True, tenant_id=B)
        assert receipt["removed"]["libraries"]["documents"] >= 1
        assert admin.exports.create(everything=True, tenant_id=B)["collections"] == []
    with docs.client("erin", B) as erin:
        assert erin.memory.query(user_id="erin") == []
    # the other tenant is untouched
    with docs.client("frank", A) as frank:
        assert len(frank.memory.query(user_id="frank")) == 1
    assert docs.app.state.services.store.verify_audit()["ok"]
