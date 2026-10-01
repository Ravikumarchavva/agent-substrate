"""Invariant register — identity and tenancy (rows I1-I4).

These are the security rows. They are written as behaviour, not as API shape,
so they stay meaningful when step 5 replaces the call sites with scope-bound
handles: the assertion is "tenant A's data never reaches tenant B", which is
true or false regardless of how the store is addressed.

The audit reproduced every leak asserted here against the L1 default store, so
these rows are reproductions first and a specification second.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from substrate.kernel.storage.local_memory_store import LocalFilesystemMemoryStore
from substrate.kernel.abstractions.storage.memory import MemoryNamespace, MemoryQuery, MemoryRecord


@pytest.mark.xfail(
    strict=True,
    reason="I3: MemoryStore.get/delete/touch take a bare id, so one tenant can "
    "read another's record by guessing or reusing an id. Fixed in step 5 "
    "(scope-bound handles).",
)
async def test_i03_a_record_is_not_readable_from_another_tenant(tmp_path: Path) -> None:
    """Ids come from request bodies and model output. An id alone must never be
    enough to address a record."""
    store = LocalFilesystemMemoryStore(tmp_path)
    private = MemoryRecord.from_text("alice's medical note", tenant_id="acme", user_id="alice")
    await store.save(private)

    # Whatever the eventual signature, reading this record as another tenant
    # must not return it.
    leaked = await store.get(private.id)
    assert leaked is None or leaked.namespace.tenant_id == "acme", (
        "a bare id returned another tenant's record"
    )
    assert await store.delete(private.id) is False, (
        "a bare id deleted another tenant's record"
    )


@pytest.mark.xfail(
    strict=True,
    reason="I3: an id supplied by a caller overwrites an existing record "
    "belonging to a different tenant. Fixed in step 5.",
)
async def test_i03_one_tenant_cannot_overwrite_another_tenants_record(tmp_path: Path) -> None:
    """``save`` is an upsert keyed by id, and the id is caller-supplied."""
    store = LocalFilesystemMemoryStore(tmp_path)
    original = MemoryRecord.from_text("acme's fact", tenant_id="acme", user_id="alice")
    await store.save(original)

    attacker = MemoryRecord.from_text(
        "attacker content", id=original.id, tenant_id="evilcorp", user_id="mallory"
    )
    await store.save(attacker)

    survivor = await store.get(original.id)
    assert survivor is not None and survivor.namespace.tenant_id == "acme", (
        "another tenant's save replaced this tenant's record"
    )


@pytest.mark.xfail(
    strict=True,
    reason="I3: MemoryNamespace treats user_id=None as a wildcard, so a query "
    "that merely omits the user reads every user in the tenant. Fixed in step 5 "
    "(no implicit wildcard; tenant_wide must be explicit).",
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


@pytest.mark.xfail(
    strict=True,
    reason="I4: no store exposes erase(scope) and the run journal is never "
    "erased at all, so a deletion request cannot be satisfied. Fixed in step 5.",
)
async def test_i04_a_scope_can_be_erased_completely(tmp_path: Path) -> None:
    """A deletion request has to reach every store, including the journal that
    holds the raw conversation. Today the GDPR eraser touches neither memory nor
    the event log."""
    store = LocalFilesystemMemoryStore(tmp_path)
    await store.save(MemoryRecord.from_text("alice's secret", tenant_id="acme", user_id="alice"))

    erase = getattr(store, "erase", None)
    assert erase is not None, "store exposes no erase(scope)"
    await erase(MemoryNamespace(tenant_id="acme", user_id="alice"))

    residue = [p for p in tmp_path.rglob("*") if p.is_file() and b"secret" in p.read_bytes()]
    assert not residue, f"erased content still on disk: {residue}"
