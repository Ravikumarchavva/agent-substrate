"""Behaviours of the store's memory beyond the shared conformance suite."""

from __future__ import annotations

from pathlib import Path

import pytest

from substrate.stores import Store
from substrate.types import TextBlock
from substrate.stores import MemoryCategory, MemoryNamespace, MemoryQuery, MemoryRecord


def _record(
    text: str, *, tenant="t1", user=None, category=MemoryCategory.SEMANTIC
) -> MemoryRecord:
    return MemoryRecord(
        content=[TextBlock(text=text)],
        category=category,
        namespace=MemoryNamespace(tenant_id=tenant, user_id=user),
    )


async def test_query_text_matching_and_min_score(tmp_path: Path) -> None:
    store = Store.at(tmp_path).memory
    await store.save(_record("likes pizza"))
    await store.save(_record("likes sushi"))

    results = await store.query(
        MemoryQuery(namespace=MemoryNamespace(tenant_id="t1"), text_query="pizza")
    )
    assert len(results) == 1
    assert "pizza" in results[0].content[0].text  # type: ignore[union-attr]
    assert results[0].score > 0 and results[0].retrieval_method == "fulltext"

    # ``min_score`` drops what ranks below it.
    spec = MemoryQuery(
        namespace=MemoryNamespace(tenant_id="t1"),
        text_query="pizza",
        min_score=results[0].score + 1,
    )
    assert await store.query(spec) == []


async def test_search_matches_word_forms_and_ranks_the_better_match_first(
    tmp_path: Path,
) -> None:
    store = Store.at(tmp_path).memory
    await store.save(
        _record("I run every morning before work and then I cook a long breakfast")
    )
    await store.save(_record("running"))

    results = await store.query(
        MemoryQuery(namespace=MemoryNamespace(tenant_id="t1"), text_query="runs")
    )

    assert [m.text for m in results] == [
        "running",
        "I run every morning before work and then I cook a long breakfast",
    ]
    assert results[0].score > results[1].score


async def test_records_survive_restart(tmp_path: Path) -> None:
    store1 = Store.at(tmp_path).memory
    rec = _record("persisted")
    rid = await store1.save(rec)

    store2 = Store.at(tmp_path).memory
    got = await store2.get(rec.namespace, rid)
    assert got is not None
    assert got.content[0].text == "persisted"  # type: ignore[union-attr]


@pytest.mark.parametrize(
    "query",
    [
        '" OR 1=1 --',
        "*",
        "tea AND",
        "NEAR(a b)",
        "text:tea",
        "tea*",
        "(",
        '"',
        "a OR b",
        "-tea",
        "^tea",
    ],
)
async def test_a_search_is_only_ever_its_words_never_search_syntax(
    tmp_path: Path, query: str
) -> None:
    """The query text comes from a model or a request body. Each word is quoted before it reaches the index, so
    operators, column filters, prefixes and unbalanced quotes are inert: no error, and no widening of what is found."""
    store = Store.at(tmp_path).memory
    mine = MemoryNamespace(tenant_id="t1", user_id="alice")
    await store.save(MemoryRecord.from_text("likes tea", namespace=mine))
    await store.save(
        MemoryRecord.from_text(
            "secret of bob", namespace=MemoryNamespace(tenant_id="t1", user_id="bob")
        )
    )

    found = await store.query(MemoryQuery(namespace=mine, text_query=query))

    assert all(m.namespace.user_id != "bob" for m in found)


async def test_erasing_removes_the_words_from_the_search_index_too(
    tmp_path: Path,
) -> None:
    """The text lives twice — in the row and, as words, in the full-text index. Erasing has to reach both: the words are
    unsearchable and no longer in any file of the store, while a bystander's record is untouched."""
    store_handle = Store.at(tmp_path / "store")
    store = store_handle.memory
    gone, kept = (
        MemoryNamespace(tenant_id="t1", user_id="alice"),
        MemoryNamespace(tenant_id="t1", user_id="bob"),
    )
    await store.save(
        MemoryRecord.from_text("xylophonic quartzite fixtures", namespace=gone)
    )
    await store.save(MemoryRecord.from_text("harmonica and banjo", namespace=kept))

    assert await store.erase(gone) == 1

    assert await store.query(MemoryQuery(namespace=gone, text_query="xylophonic")) == []
    assert [
        m.text
        for m in await store.query(MemoryQuery(namespace=kept, text_query="banjo"))
    ] == ["harmonica and banjo"]
    await store_handle.aclose()
    left = [
        p.name
        for p in (tmp_path / "store").rglob("*")
        if p.is_file() and b"xylophon" in p.read_bytes()
    ]
    assert left == []
