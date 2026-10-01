"""Tests for LocalFilesystemMemoryStore (kernel MemoryStore Protocol)."""

from __future__ import annotations

from pathlib import Path

from substrate.agents.storage import LocalFilesystemMemoryStore
from substrate.kernel.core.content import TextBlock
from substrate.kernel.storage.memory import (
    MemoryCategory,
    MemoryNamespace,
    MemoryQuery,
    MemoryRecord,
    MemoryStatus,
)


def _record(text: str, *, tenant="t1", user=None, category=MemoryCategory.SEMANTIC) -> MemoryRecord:
    return MemoryRecord(
        content=[TextBlock(text=text)],
        category=category,
        namespace=MemoryNamespace(tenant_id=tenant, user_id=user),
    )


async def test_save_get_delete_round_trip(tmp_path: Path) -> None:
    store = LocalFilesystemMemoryStore(root=tmp_path)
    rec = _record("likes pizza")

    rid = await store.save(rec)
    assert rid == rec.id

    got = await store.get(rid)
    assert got is not None
    assert got.content[0].text == "likes pizza"  # type: ignore[union-attr]

    assert await store.delete(rid) is True
    assert await store.get(rid) is None
    assert await store.delete(rid) is False


async def test_tenant_isolation(tmp_path: Path) -> None:
    store = LocalFilesystemMemoryStore(root=tmp_path)
    r1 = await store.save(_record("a", tenant="tenant-a"))
    r2 = await store.save(_record("b", tenant="tenant-b"))

    results_a = await store.query(MemoryQuery(namespace=MemoryNamespace(tenant_id="tenant-a")))
    assert {m.id for m in results_a} == {r1}

    results_b = await store.query(MemoryQuery(namespace=MemoryNamespace(tenant_id="tenant-b")))
    assert {m.id for m in results_b} == {r2}


async def test_query_text_matching_and_min_score(tmp_path: Path) -> None:
    store = LocalFilesystemMemoryStore(root=tmp_path)
    await store.save(_record("likes pizza"))
    await store.save(_record("likes sushi"))

    results = await store.query(
        MemoryQuery(namespace=MemoryNamespace(tenant_id="t1"), text_query="pizza")
    )
    assert len(results) == 1
    assert "pizza" in results[0].content[0].text  # type: ignore[union-attr]
    assert results[0].score == 1.0


async def test_query_filters_by_category_and_status(tmp_path: Path) -> None:
    store = LocalFilesystemMemoryStore(root=tmp_path)
    active = await store.save(_record("keep", category=MemoryCategory.EPISODIC))
    other_cat = await store.save(_record("skip cat"))

    results = await store.query(
        MemoryQuery(
            namespace=MemoryNamespace(tenant_id="t1"),
            categories=[MemoryCategory.EPISODIC],
        )
    )
    assert {m.id for m in results} == {active}
    assert other_cat not in {m.id for m in results}

    superseded = _record("superseded").model_copy(update={"status": MemoryStatus.SUPERSEDED})
    await store.save(superseded)
    results_active_only = await store.query(MemoryQuery(namespace=MemoryNamespace(tenant_id="t1")))
    assert superseded.id not in {m.id for m in results_active_only}


async def test_touch_updates_access_metadata(tmp_path: Path) -> None:
    store = LocalFilesystemMemoryStore(root=tmp_path)
    rec = _record("hi")
    rid = await store.save(rec)

    before = await store.get(rid)
    assert before.access_count == 0  # type: ignore[union-attr]

    await store.touch([rid])
    after = await store.get(rid)
    assert after.access_count == 1  # type: ignore[union-attr]
    assert after.last_accessed_at is not None  # type: ignore[union-attr]


async def test_clear_purges_namespace(tmp_path: Path) -> None:
    store = LocalFilesystemMemoryStore(root=tmp_path)
    r1 = await store.save(_record("a", user="u1"))
    r2 = await store.save(_record("b", user="u2"))

    await store.clear(MemoryNamespace(tenant_id="t1", user_id="u1"))
    assert await store.get(r1) is None
    assert await store.get(r2) is not None


async def test_records_survive_restart(tmp_path: Path) -> None:
    store1 = LocalFilesystemMemoryStore(root=tmp_path)
    rid = await store1.save(_record("persisted"))

    store2 = LocalFilesystemMemoryStore(root=tmp_path)
    got = await store2.get(rid)
    assert got is not None
    assert got.content[0].text == "persisted"  # type: ignore[union-attr]
