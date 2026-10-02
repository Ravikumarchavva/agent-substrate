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
