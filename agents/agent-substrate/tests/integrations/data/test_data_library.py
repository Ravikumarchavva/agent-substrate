"""``DataLibrary`` against a real substrate-data (in this process): the ``DocumentLibrary`` conformance suite, lexical and semantic."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from substrate.documents.enrichment import FirstSentenceEnricher, enrich
from substrate.integrations.data import DataLibrary, DataServiceConnection
from substrate.testing.conformance.document_library import (
    DocumentLibraryConformance,
    document,
    prose,
    unique,
)
from substrate.types.errors import AuthError, ServiceUnavailableError

from .conftest import DocsService, dead_url

pytestmark = pytest.mark.integration
TENANT = "t1"


def conformance_collection() -> str:
    return f"tenants/{TENANT}/conformance/{uuid.uuid4().hex[:12]}/documents"


class _OnService(DocumentLibraryConformance):
    semantic = False

    @pytest.fixture
    def collection(self) -> str:
        return conformance_collection()

    @pytest.fixture
    def other(self) -> str:
        return conformance_collection()

    @pytest.fixture
    async def library(self, docs_service: DocsService) -> AsyncIterator[DataLibrary]:
        connection = DataServiceConnection(docs_service.url)
        yield DataLibrary(
            connection, docs_service.tokens(), semantic=self.semantic, file_topics=True
        )
        await connection.aclose()


class TestDataLibrary(_OnService):
    """The very suite the folder ``Library`` runs, unchanged."""


class TestDataLibraryWithAnEmbedder(_OnService):
    semantic = True
    has_embedder = True


@pytest.fixture
async def library(docs_service: DocsService) -> AsyncIterator[DataLibrary]:
    connection = DataServiceConnection(docs_service.url)
    yield DataLibrary(connection, docs_service.tokens())
    await connection.aclose()


async def test_a_user_collection_is_private_to_that_user(docs_service: DocsService):
    connection = DataServiceConnection(docs_service.url)
    try:
        alice = DataLibrary(connection, docs_service.tokens())
        name = f"tenants/{TENANT}/users/alice/conversations/{uuid.uuid4().hex[:8]}/documents"
        added = await alice.add(
            document(prose(unique("tulip"))), "a.md", collection=name
        )
        assert await alice.info(name, added.document) is not None

        def as_user(tenant: str, user: str | None) -> str:
            return docs_service.tokens()(tenant, "bob" if user == "alice" else user)

        bob = DataLibrary(
            connection, as_user
        )  # a token for another subject, asked to read alice's folder
        assert await bob.info(name, added.document) is None
        assert (await bob.list(collection=name)).documents == ()
        assert await bob.delete(collection=name, document=added.document) is False
        assert await alice.info(name, added.document) is not None
    finally:
        await connection.aclose()


async def test_enrichment_runs_over_the_adapter(library: DataLibrary):
    """``substrate.documents.enrichment.enrich`` is written against the port: it works on the adapter as it does on the folder library."""
    name = conformance_collection()
    added = await library.add(
        document(prose(unique("moss")), title="Moss"), "moss.md", collection=name
    )
    done = await enrich(
        library,
        collection=name,
        document=added.document,
        enricher=FirstSentenceEnricher(),
    )
    assert done.state == "done"
    info = await library.info(name, added.document)
    assert info is not None and info.enrichment == "done" and info.description


async def test_a_token_without_the_scope_is_an_auth_error(docs_service: DocsService):
    connection = DataServiceConnection(docs_service.url)
    try:
        weak = DataLibrary(connection, docs_service.tokens(("files:write",)))
        with pytest.raises(AuthError):
            await weak.add(
                document(prose("x")), "a.md", collection=conformance_collection()
            )
    finally:
        await connection.aclose()


async def test_an_unreachable_service_is_a_transient_error():
    connection = DataServiceConnection(dead_url(), timeout=1)
    try:
        library = DataLibrary(connection, lambda tenant, user: "t")
        with pytest.raises(ServiceUnavailableError):
            await library.list(collection=conformance_collection())
    finally:
        await connection.aclose()


async def test_the_audit_log_names_collections_by_id_only(
    docs_service: DocsService, library: DataLibrary
):
    name = conformance_collection()
    secret = unique("zulu")
    await library.add(document(prose(secret)), f"{secret}.md", collection=name)
    events = docs_service.app.state.services.store.audit_events(None, 500)
    dump = str(events)
    assert name not in dump and secret not in dump
