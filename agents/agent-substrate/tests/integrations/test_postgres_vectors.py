"""What the PostgreSQL vectors do that the folder store's do not: pgvector's HNSW index, per vector width.

Needs Postgres with pgvector (``make infra-up``); skipped when unreachable.
"""

from __future__ import annotations

import random

import pytest

from substrate.stores import Document
from tests._postgres import schema_store

pytestmark = [pytest.mark.requires_postgres]


def _docs(count: int, dims: int, *, seed: int = 1) -> list[Document]:
    rng = random.Random(seed)
    return [
        Document.from_text(
            f"doc {i}", id=f"d{i}", embedding=[rng.random() - 0.5 for _ in range(dims)]
        )
        for i in range(count)
    ]


async def _indexes(store) -> dict[str, str]:
    async with store.database.transaction() as tx:
        rows = await tx.fetchall(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'vector_docs' AND schemaname = current_schema()"
        )
    return {row["indexname"]: row["indexdef"] for row in rows}


async def test_each_width_of_vector_gets_its_own_hnsw_index_and_wide_ones_are_indexed_at_half_precision(
    tmp_path,
) -> None:
    async for store in schema_store(tmp_path):
        await store.vectors.add(_docs(5, 8), collection="text")
        await store.vectors.add(_docs(5, 2048), collection="image")
        indexes = await _indexes(store)
        assert (
            "hnsw" in indexes["vector_ann_8"]
            and "::vector(8)" in indexes["vector_ann_8"]
        )
        assert (
            "hnsw" in indexes["vector_ann_2048"]
            and "halfvec(2048)" in indexes["vector_ann_2048"]
        )


async def test_search_over_an_indexed_collection_finds_the_nearest(tmp_path) -> None:
    async for store in schema_store(tmp_path):
        docs = _docs(600, 16)
        await store.vectors.add(docs, collection="kb")
        for probe in (3, 42, 599):
            assert [
                r.id
                for r in await store.vectors.search(
                    docs[probe].embedding, collection="kb", limit=1
                )
            ] == [f"d{probe}"]


async def test_a_filter_that_drops_most_candidates_still_fills_the_limit(
    tmp_path,
) -> None:
    async for store in schema_store(tmp_path):
        docs = _docs(400, 16)
        for i, doc in enumerate(docs):
            doc.metadata["group"] = "rare" if i % 100 == 0 else "common"
        await store.vectors.add(docs, collection="kb")
        results = await store.vectors.search(
            docs[7].embedding, collection="kb", limit=4, filter={"group": "rare"}
        )
        assert len(results) == 4 and all(r.metadata["group"] == "rare" for r in results)


async def test_an_embedding_comes_back_as_it_went_in(tmp_path) -> None:
    async for store in schema_store(tmp_path):
        vector = [0.25, -0.5, 0.125, 1.0]
        await store.vectors.add(
            [Document.from_text("x", id="x", embedding=vector)], collection="kb"
        )
        assert (await store.vectors.get(["x"], collection="kb"))[
            0
        ].embedding == pytest.approx(vector)
