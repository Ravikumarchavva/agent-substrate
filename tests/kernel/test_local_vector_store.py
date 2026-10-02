"""Behaviours of the store's vectors beyond the shared conformance suite."""

from __future__ import annotations


from substrate.stores import Store
from substrate.stores import Document


async def test_a_document_with_no_embedding_is_stored_without_one_and_the_store_does_not_embed_it(tmp_path):
    store = Store.at(tmp_path).vectors
    await store.add([Document.from_text("no vector", id="d")], collection="kb")
    assert [(d.id, d.embedding) for d in await store.get(["d"], collection="kb")] == [("d", None)]
    assert await store.search([1.0, 0.0], collection="kb") == []  # nothing to compare
    assert [d.id for d in await store.unembedded(collection="kb")] == ["d"]


async def test_a_store_written_before_spaces_existed_is_migrated_in_place(tmp_path):
    """A folder written by the first schema version opens, gains ``vector_spaces`` and keeps its vectors."""
    import sqlite3

    from substrate.stores.vector_tables import _vector_schema
    from substrate.stores.sqlite import SqliteDatabase

    root = tmp_path / "old"
    root.mkdir()
    database = SqliteDatabase(root / "substrate.db")
    await database.start()
    await database.script(_vector_schema(database).replace("{pk}", database.auto_pk))
    await database.script("CREATE TABLE IF NOT EXISTS substrate_migrations (component TEXT NOT NULL, version INTEGER NOT NULL, applied_at DOUBLE PRECISION NOT NULL, PRIMARY KEY (component, version));")
    await database.aclose()
    con = sqlite3.connect(root / "substrate.db")
    con.execute("INSERT INTO substrate_migrations VALUES ('vectors', 1, 0)")
    con.commit()
    con.close()

    store = Store.at(root)
    await store.start()
    try:
        vectors = store.vectors
        await vectors.add([Document.from_text("hello", id="d1", embedding=[1.0, 0.0])], collection="kb", space="m")
        assert await vectors.space_of("kb") == ("m", 2)
    finally:
        await store.aclose()


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
