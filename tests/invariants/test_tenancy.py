"""Invariant register — identity and tenancy (rows I1-I4).

These are the security rows. They are written as behaviour, not as API shape:
the assertion is "tenant A's data never reaches tenant B", which is true or false
regardless of how the store is addressed.

The audit reproduced every leak asserted here against the L1 default store, so
these rows are reproductions first and a specification second. Each is stated here
against the default store; the same guarantees, in full, are the memory-store
conformance suite (``kernel/testing/conformance/memory_store.py``) that every
implementation — local filesystem, Postgres, Lance — has to pass.
"""

from __future__ import annotations

from pathlib import Path


from substrate.kernel.storage.local_memory_store import LocalFilesystemMemoryStore
from substrate.kernel.abstractions.storage.memory import MemoryNamespace, MemoryQuery, MemoryRecord


async def test_i03_a_record_is_not_readable_from_another_tenant(tmp_path: Path) -> None:
    """Ids come from request bodies and model output. An id alone must never be
    enough to address a record."""
    store = LocalFilesystemMemoryStore(tmp_path)
    private = MemoryRecord.from_text("alice's medical note", tenant_id="acme", user_id="alice")
    await store.save(private)

    # Whatever the eventual signature, reading this record as another tenant
    # must not return it.
    stranger = MemoryNamespace(tenant_id="evilcorp", user_id="alice")
    assert await store.get(stranger, private.id) is None, "an id returned another tenant's record"
    assert await store.delete(stranger, private.id) is False, "an id deleted another tenant's record"


async def test_i03_one_tenant_cannot_overwrite_another_tenants_record(tmp_path: Path) -> None:
    """``save`` is an upsert keyed by id, and the id is caller-supplied."""
    store = LocalFilesystemMemoryStore(tmp_path)
    original = MemoryRecord.from_text("acme's fact", tenant_id="acme", user_id="alice")
    await store.save(original)

    attacker = MemoryRecord.from_text(
        "attacker content", id=original.id, tenant_id="evilcorp", user_id="mallory"
    )
    await store.save(attacker)

    survivor = await store.get(MemoryNamespace(tenant_id="acme", user_id="alice"), original.id)
    assert survivor is not None and survivor.text == "acme's fact", (
        "another tenant's save replaced this tenant's record"
    )


async def test_i03_omitting_a_scope_field_is_not_a_wildcard(tmp_path: Path) -> None:
    """The dangerous default: forgetting a field widens the query instead of
    narrowing it, and nothing in the type system notices."""
    store = LocalFilesystemMemoryStore(tmp_path)
    await store.save(MemoryRecord.from_text("alice's secret", tenant_id="acme", user_id="alice"))

    hits = await store.query(MemoryQuery(namespace=MemoryNamespace(tenant_id="acme")))
    assert hits == [], (
        "a query that omitted user_id returned another user's records: "
        f"{[h.record.text for h in hits]}"
    )


async def test_i04_a_scope_can_be_erased_completely(tmp_path: Path) -> None:
    """A deletion request has to reach every store, including the journal that
    holds the raw conversation. Today the GDPR eraser touches neither memory nor
    the event log."""
    store = LocalFilesystemMemoryStore(tmp_path)
    await store.save(MemoryRecord.from_text("alice's secret", tenant_id="acme", user_id="alice"))

    assert await store.erase(MemoryNamespace(tenant_id="acme", user_id="alice")) == 1

    residue = [p for p in tmp_path.rglob("*") if p.is_file() and b"secret" in p.read_bytes()]
    assert not residue, f"erased content still on disk: {residue}"
