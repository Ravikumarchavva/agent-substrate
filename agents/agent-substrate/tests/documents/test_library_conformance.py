"""The folder ``Library`` held to the ``DocumentLibrary`` conformance suite: on SQLite, with an embedder, and on PostgreSQL (skipped when none is reachable)."""

from __future__ import annotations

import pytest

from substrate.documents import DocumentError, ExtractedImage, Library, Reader
from substrate.stores import Store
from substrate.testing.conformance.document_library import (
    DocumentLibraryConformance,
    document,
    png,
    prose,
)
from tests._postgres import schema_store
from tests.documents.test_library_embeddings import FakeEmbedder


def _library(store: Store, **options) -> Library:
    return Library(store, reader=Reader(isolate=False), file_topics=True, **options)


class TestLibraryOnSqlite(DocumentLibraryConformance):
    @pytest.fixture
    async def library(self, tmp_path):
        store = Store.at(tmp_path / "store")
        await store.start()
        try:
            yield _library(store)
        finally:
            await store.aclose()


class TestLibraryWithAnEmbedderOnSqlite(DocumentLibraryConformance):
    has_embedder = True

    @pytest.fixture
    async def library(self, tmp_path):
        store = Store.at(tmp_path / "store")
        await store.start()
        try:
            yield _library(store, embedder=FakeEmbedder())
        finally:
            await store.aclose()


@pytest.mark.requires_postgres
class TestLibraryOnPostgres(DocumentLibraryConformance):
    @pytest.fixture
    async def library(self, tmp_path):
        async for store in schema_store(tmp_path):
            yield _library(store)


# ── the steps ``add`` is made of ──────────────────────────────────────────────


@pytest.fixture
async def folder_library(tmp_path):
    store = Store.at(tmp_path / "store")
    await store.start()
    try:
        yield _library(store)
    finally:
        await store.aclose()


C = "tenants/acme/documents"


async def test_prepare_touches_no_storage_and_file_then_index_make_it_findable(
    folder_library,
):
    library = folder_library
    bundle = await library.prepare(document(prose("harbour")), "port.md")
    assert bundle.document == Library.document_id("port.md", bundle.sha256)
    assert bundle.files[-1].path == "index.md"
    assert await library._files.list_prefix(C + "/") == []
    assert (await library.list(collection=C)).documents == ()

    await library.file(bundle, collection=C)
    assert (
        await library.info(C, bundle.document) is None
    )  # filed, not yet in the catalog
    assert await library.index(C, bundle.document) == 0
    info = await library.info(C, bundle.document)
    assert info is not None and info.sections == len(bundle.sections)
    assert await library.find(collection=C, query="harbour")


async def test_add_is_the_same_as_prepare_file_and_index(folder_library):
    library = folder_library
    figure = ExtractedImage(data=png(), id="fig-1", caption="A chart", page_number=1)
    source = document(prose("quarter"), images=[figure])
    added = await library.add(source, "q.md", collection=C)
    before = sorted(k for k, _s, _m in await library._files.list_prefix(C + "/"))
    # indexing from the files alone gives the same documents and sections
    assert await library.reindex(collection=C) == 1
    after = sorted(k for k, _s, _m in await library._files.list_prefix(C + "/"))
    assert before == after
    info = await library.info(C, added.document)
    assert info is not None and info.sections == added.sections
    assert info.images == added.images == 1


async def test_indexing_a_document_that_was_never_filed_is_an_error(folder_library):
    with pytest.raises(DocumentError):
        await folder_library.index(C, "missing-abc123")
