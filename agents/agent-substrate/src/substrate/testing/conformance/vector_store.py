"""Conformance suite for ``VectorStore``.

Every implementation — in-memory, local filesystem, pgvector, LanceDB, any a consumer writes —
runs exactly these tests. They assert what the port promises: documents live in a collection and
never leak across collections, ``upsert`` replaces by id, search ranks by similarity, and a hostile
id or collection name is data, never part of a query.

Subclass it, provide the ``store`` fixture, and set ``dimensions`` if the store's vectors have a
fixed width::

    class TestMyStore(VectorStoreConformance):
        dimensions = 384

        @pytest.fixture
        async def store(self): ...
"""

from __future__ import annotations

import pytest

from substrate.types.content import TextBlock
from substrate.stores.vector import Document, VectorStore


class VectorStoreConformance:
    dimensions: int = 4

    @pytest.fixture
    async def store(self) -> VectorStore:  # pragma: no cover - supplied by subclasses
        raise NotImplementedError

    # -- helpers -------------------------------------------------------------

    def vec(self, *hot: int) -> list[float]:
        """A vector with 1.0 at each index in ``hot`` and zero elsewhere."""
        v = [0.0] * self.dimensions
        for i in hot:
            v[i] = 1.0
        return v

    def doc(self, id: str, text: str, *hot: int, **metadata: object) -> Document:
        return Document(id=id, content=[TextBlock(text=text)], embedding=self.vec(*hot), metadata=dict(metadata))

    # ==================================================================== round trip

    async def test_added_documents_come_back_by_id(self, store: VectorStore) -> None:
        ids = await store.add([self.doc("a", "alpha", 0, tag="x"), self.doc("b", "beta", 1)], collection="c")
        assert sorted(ids) == ["a", "b"]
        got = {d.id: d for d in await store.get(["a", "b"], collection="c")}
        assert got["a"].to_text() == "alpha" and got["a"].metadata == {"tag": "x"}
        assert got["b"].to_text() == "beta"

    async def test_getting_a_missing_id_returns_nothing_for_it(self, store: VectorStore) -> None:
        await store.add([self.doc("a", "alpha", 0)], collection="c")
        assert [d.id for d in await store.get(["a", "nope"], collection="c")] == ["a"]

    async def test_upsert_replaces_by_id_without_duplicating(self, store: VectorStore) -> None:
        await store.add([self.doc("a", "old", 0)], collection="c")
        await store.upsert([self.doc("a", "new", 1)], collection="c")
        docs = await store.get(["a"], collection="c")
        assert [d.to_text() for d in docs] == ["new"]
        assert [r.id for r in await store.search(self.vec(1), collection="c", limit=10)] == ["a"]

    # ==================================================================== search

    async def test_search_ranks_the_closest_document_first_and_respects_the_limit(self, store: VectorStore) -> None:
        await store.add(
            [self.doc("near", "n", 0), self.doc("mid", "m", 0, 1), self.doc("far", "f", 2)],
            collection="c",
        )
        results = await store.search(self.vec(0), collection="c", limit=2)
        assert [r.id for r in results][0] == "near" and len(results) == 2
        assert results[0].score >= results[1].score, "results must be ranked best-first"

    async def test_search_filters_on_metadata(self, store: VectorStore) -> None:
        await store.add([self.doc("a", "a", 0, kind="red"), self.doc("b", "b", 0, kind="blue")], collection="c")
        results = await store.search(self.vec(0), collection="c", limit=10, filter={"kind": "blue"})
        assert [r.id for r in results] == ["b"]

    async def test_searching_an_empty_or_unknown_collection_finds_nothing(self, store: VectorStore) -> None:
        assert await store.search(self.vec(0), collection="never-created", limit=5) == []

    # ==================================================================== collections

    async def test_collections_never_leak_into_each_other(self, store: VectorStore) -> None:
        await store.add([self.doc("a", "in one", 0)], collection="one")
        await store.add([self.doc("a", "in two", 0)], collection="two")
        assert [d.to_text() for d in await store.get(["a"], collection="one")] == ["in one"]
        assert [d.to_text() for d in await store.get(["a"], collection="two")] == ["in two"]
        assert [r.to_text() for r in await store.search(self.vec(0), collection="one", limit=10)] == ["in one"]

    async def test_deleting_removes_only_the_named_documents_and_counts_them(self, store: VectorStore) -> None:
        await store.add([self.doc("a", "a", 0), self.doc("b", "b", 1), self.doc("c", "c", 2)], collection="col")
        assert await store.delete(["a", "missing"], collection="col") == 1
        assert sorted(d.id for d in await store.get(["a", "b", "c"], collection="col")) == ["b", "c"]

    async def test_deleting_in_one_collection_leaves_the_same_id_in_another(self, store: VectorStore) -> None:
        await store.add([self.doc("a", "keep", 0)], collection="keep")
        await store.add([self.doc("a", "drop", 0)], collection="drop")
        await store.delete(["a"], collection="drop")
        assert len(await store.get(["a"], collection="keep")) == 1

    async def test_collections_are_listed_and_can_be_deleted(self, store: VectorStore) -> None:
        await store.add([self.doc("a", "a", 0)], collection="listed")
        assert "listed" in await store.list_collections()
        assert await store.delete_collection("listed") == 1
        assert "listed" not in await store.list_collections()
        assert await store.get(["a"], collection="listed") == []

    async def test_renaming_a_collection_moves_its_documents(self, store: VectorStore) -> None:
        await store.add([self.doc("a", "a", 0), self.doc("b", "b", 1)], collection="before")
        assert await store.rename_collection("before", "after") == 2
        assert sorted(d.id for d in await store.get(["a", "b"], collection="after")) == ["a", "b"]
        assert await store.get(["a", "b"], collection="before") == []

    async def test_renaming_a_collection_that_does_not_exist_moves_nothing(self, store: VectorStore) -> None:
        assert await store.rename_collection("no-such", "other") == 0

    # ==================================================================== hostile names

    @pytest.mark.parametrize("hostile", ["x' OR '1'='1", "a'; DROP TABLE t; --", "../../etc/passwd", "a/b\\c", "..", "ünï-çødé"])
    async def test_a_hostile_document_id_is_inert(self, store: VectorStore, hostile: str) -> None:
        await store.add([self.doc("bystander", "bystander", 1)], collection="c")
        await store.add([self.doc(hostile, "mine", 0)], collection="c")
        assert [d.to_text() for d in await store.get([hostile], collection="c")] == ["mine"]
        assert await store.delete([hostile], collection="c") == 1
        assert [d.id for d in await store.get(["bystander"], collection="c")] == ["bystander"]

    @pytest.mark.parametrize("hostile", ["x' OR '1'='1", "a'; DROP TABLE t; --", "../../etc", "ünï-çødé"])
    async def test_a_hostile_collection_name_is_inert(self, store: VectorStore, hostile: str) -> None:
        await store.add([self.doc("a", "victim", 0)], collection="victim")
        await store.add([self.doc("a", "attacker", 0)], collection=hostile)
        assert [d.to_text() for d in await store.get(["a"], collection=hostile)] == ["attacker"]
        assert [d.to_text() for d in await store.get(["a"], collection="victim")] == ["victim"]
        await store.delete_collection(hostile)
        assert [d.to_text() for d in await store.get(["a"], collection="victim")] == ["victim"]


class SearchableVectorStoreConformance(VectorStoreConformance):
    """What a store that also searches by words — ``lexical_search`` and ``hybrid_search``, beyond the port — promises:
    word forms match, a query is only ever words, the two rankings fuse by rank, and a vector with no direction scores
    zero instead of breaking the search. A collection holds one width of vector."""

    async def test_a_collection_holds_one_width_of_vector(self, store) -> None:
        await store.add([Document.from_text("a", id="a", embedding=[1.0, 0.0])], collection="kb")
        with pytest.raises(ValueError, match="2-wide"):
            await store.add([Document.from_text("b", id="b", embedding=[1.0, 0.0, 0.0])], collection="kb")
        # the refused write left nothing behind, and another collection is free to use another width
        assert [d.id for d in await store.get(["a", "b"], collection="kb")] == ["a"]
        await store.add([Document.from_text("c", id="c", embedding=[1.0, 0.0, 0.0])], collection="wide")

    async def test_adding_does_not_replace_but_upserting_does(self, store) -> None:
        await store.add([Document.from_text("first", id="d", embedding=[1.0, 0.0])], collection="kb")
        await store.add([Document.from_text("second", id="d", embedding=[0.0, 1.0])], collection="kb")
        assert (await store.get(["d"], collection="kb"))[0].to_text() == "first"
        await store.upsert([Document.from_text("third", id="d", embedding=[0.0, 1.0])], collection="kb")
        assert (await store.get(["d"], collection="kb"))[0].to_text() == "third"

    async def test_upserting_one_id_twice_in_a_call_keeps_the_last(self, store) -> None:
        ids = await store.upsert(
            [
                Document.from_text("first", id="dup", embedding=[1.0, 0.0]),
                Document.from_text("second (should win)", id="dup", embedding=[1.0, 0.0]),
            ],
            collection="kb",
        )
        assert ids == ["dup", "dup"]
        assert [d.to_text() for d in await store.get(["dup"], collection="kb")] == ["second (should win)"]

    async def test_lexical_search_finds_stemmed_words_and_treats_the_query_as_words_only(self, store) -> None:
        await store.add(
            [
                Document.from_text("Quarterly revenue grew while costs were running flat", id="a", embedding=[1.0, 0.0]),
                Document.from_text("The cat sat on the mat", id="b", embedding=[0.0, 1.0]),
            ],
            collection="kb",
        )
        assert [r.id for r in await store.lexical_search("run revenue", collection="kb")] == ["a"]
        for syntax in ['"', "revenue OR cat", "NEAR(a b)", "rev*", "text:cat", "-cat"]:
            assert all(r.id in {"a", "b"} for r in await store.lexical_search(syntax, collection="kb"))
        assert await store.lexical_search("OR OR", collection="kb") == []

    async def test_a_metadata_filter_narrows_dense_and_lexical_search(self, store) -> None:
        await store.add(
            [
                Document.from_text("invoice one", id="x", embedding=[1.0, 0.0], metadata={"file": "a"}),
                Document.from_text("invoice two", id="y", embedding=[0.9, 0.1], metadata={"file": "b"}),
            ],
            collection="kb",
        )
        assert [r.id for r in await store.search([1.0, 0.0], collection="kb", filter={"file": "b"})] == ["y"]
        assert [r.id for r in await store.lexical_search("invoice", collection="kb", filter={"file": "b"})] == ["y"]
        assert await store.lexical_search("invoice", collection="kb", filter={"file": "zzz"}) == []

    async def test_hybrid_search_surfaces_what_only_one_of_the_two_rankings_found(self, store) -> None:
        """The fused score looks only at rank positions. A chunk the embedding misses but the words find (and the reverse)
        is still returned, and one both rank well comes first."""
        await store.add(
            [
                Document.from_text("invoice total due", id="both", embedding=[1.0, 0.0, 0.0]),
                Document.from_text("invoice only by words", id="words", embedding=[0.0, 0.0, 1.0]),
                Document.from_text("unrelated prose", id="vector", embedding=[0.9, 0.1, 0.0]),
            ],
            collection="kb",
        )
        results = await store.hybrid_search([1.0, 0.0, 0.0], "invoice", collection="kb")
        assert [r.id for r in results][0] == "both"
        assert {r.id for r in results} == {"both", "words", "vector"}
        assert results[0].score > results[1].score
        assert [r.id for r in await store.hybrid_search([1.0, 0.0, 0.0], "invoice", collection="kb", fused_k=1)] == ["both"]
        assert await store.hybrid_search([1.0, 0.0, 0.0], "invoice", collection="kb", filter={"nope": 1}) == []

    async def test_a_zero_vector_scores_zero_instead_of_breaking_the_search(self, store) -> None:
        await store.add(
            [Document.from_text("blank", id="z", embedding=[0.0, 0.0]), Document.from_text("x", id="x", embedding=[1.0, 0.0])],
            collection="kb",
        )
        assert [r.id for r in await store.search([1.0, 0.0], collection="kb")] == ["x", "z"]
        assert [r.score for r in await store.search([0.0, 0.0], collection="kb")] == [0.0, 0.0]


__all__ = ["SearchableVectorStoreConformance", "VectorStoreConformance"]
