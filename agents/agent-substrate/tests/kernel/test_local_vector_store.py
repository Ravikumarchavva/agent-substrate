"""Behaviours of the store's vectors beyond the shared conformance suite."""

from __future__ import annotations

import pytest

from substrate.stores import Store
from substrate.stores import Document


async def test_add_missing_embedding_without_client_raises(tmp_path):
    store = Store.at(tmp_path).vectors
    doc = Document.from_text("no vector")
    with pytest.raises(ValueError):
        await store.add([doc], collection="kb")


async def test_persistence_across_instances(tmp_path):
    store1 = Store.at(tmp_path).vectors
    await store1.add(
        [Document.from_text("hello", id="d1", embedding=[1.0, 0.0])], collection="kb"
    )

    # Fresh instance pointed at the same root — simulates a process restart.
    store2 = Store.at(tmp_path).vectors
    fetched = await store2.get(["d1"], collection="kb")
    assert len(fetched) == 1
    assert fetched[0].to_text() == "hello"

    results = await store2.search([1.0, 0.0], collection="kb")
    assert results[0].id == "d1"


async def test_a_collection_holds_one_width_of_vector(tmp_path):
    store = Store.at(tmp_path).vectors
    await store.add(
        [Document.from_text("a", id="a", embedding=[1.0, 0.0])], collection="kb"
    )

    with pytest.raises(ValueError, match="2-wide"):
        await store.add(
            [Document.from_text("b", id="b", embedding=[1.0, 0.0, 0.0])],
            collection="kb",
        )
    # the refused write left nothing behind, and another collection is free to use another width
    assert [d.id for d in await store.get(["a", "b"], collection="kb")] == ["a"]
    await store.add(
        [Document.from_text("c", id="c", embedding=[1.0, 0.0, 0.0])], collection="wide"
    )


async def test_adding_does_not_replace_but_upserting_does(tmp_path):
    store = Store.at(tmp_path).vectors
    await store.add(
        [Document.from_text("first", id="d", embedding=[1.0, 0.0])], collection="kb"
    )
    await store.add(
        [Document.from_text("second", id="d", embedding=[0.0, 1.0])], collection="kb"
    )
    assert (await store.get(["d"], collection="kb"))[0].to_text() == "first"
    await store.upsert(
        [Document.from_text("third", id="d", embedding=[0.0, 1.0])], collection="kb"
    )
    assert (await store.get(["d"], collection="kb"))[0].to_text() == "third"


async def test_lexical_search_finds_stemmed_words_and_treats_the_query_as_words_only(
    tmp_path,
):
    store = Store.at(tmp_path).vectors
    await store.add(
        [
            Document.from_text(
                "Quarterly revenue grew while costs were running flat",
                id="a",
                embedding=[1.0, 0.0],
            ),
            Document.from_text("The cat sat on the mat", id="b", embedding=[0.0, 1.0]),
        ],
        collection="kb",
    )

    assert [
        r.id for r in await store.lexical_search("run revenue", collection="kb")
    ] == ["a"]
    for syntax in ['"', "revenue OR cat", "NEAR(a b)", "rev*", "text:cat", "-cat"]:
        assert all(
            r.id in {"a", "b"}
            for r in await store.lexical_search(syntax, collection="kb")
        )
    assert await store.lexical_search("OR OR", collection="kb") == []


async def test_hybrid_search_surfaces_what_only_one_of_the_two_rankings_found(tmp_path):
    """The fused score looks only at rank positions. A chunk the embedding misses but the words find (and the reverse)
    is still returned, and one both rank well comes first."""
    store = Store.at(tmp_path).vectors
    await store.add(
        [
            Document.from_text(
                "invoice total due", id="both", embedding=[1.0, 0.0, 0.0]
            ),
            Document.from_text(
                "invoice only by words", id="words", embedding=[0.0, 0.0, 1.0]
            ),
            Document.from_text(
                "unrelated prose", id="vector", embedding=[0.9, 0.1, 0.0]
            ),
        ],
        collection="kb",
    )

    results = await store.hybrid_search([1.0, 0.0, 0.0], "invoice", collection="kb")

    assert [r.id for r in results][0] == "both"
    assert {r.id for r in results} == {"both", "words", "vector"}
    assert results[0].score > results[1].score
    assert [
        r.id
        for r in await store.hybrid_search(
            [1.0, 0.0, 0.0], "invoice", collection="kb", fused_k=1
        )
    ] == ["both"]
    assert [
        r.id
        for r in await store.hybrid_search(
            [1.0, 0.0, 0.0], "invoice", collection="kb", filter={"nope": 1}
        )
    ] == []


async def test_a_zero_vector_scores_zero_instead_of_breaking_the_search(tmp_path):
    store = Store.at(tmp_path).vectors
    await store.add(
        [
            Document.from_text("blank", id="z", embedding=[0.0, 0.0]),
            Document.from_text("x", id="x", embedding=[1.0, 0.0]),
        ],
        collection="kb",
    )

    assert [r.id for r in await store.search([1.0, 0.0], collection="kb")] == ["x", "z"]
    assert [r.score for r in await store.search([0.0, 0.0], collection="kb")] == [
        0.0,
        0.0,
    ]
