"""The retrieval index on PostgreSQL with pgvector (ADR 0008): the same engine and routes as SQLite, in a `retrieval` schema."""

from __future__ import annotations

import subprocess

import pytest
from substrate.testing.conformance.document_library import prose

from conftest import WordEmbedder, WordReranker, serve_documents, start_postgres
from test_documents import extraction

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def pg_docs(identity, ticket_keys, tmp_path_factory):
    server = start_postgres("pgvector/pgvector:pg16")
    url = server.database()
    service = serve_documents(
        identity,
        ticket_keys,
        tmp_path_factory.mktemp("pg"),
        retrieval_url=url,
        embedder=WordEmbedder(),
        reranker=WordReranker(),
    )
    yield service, url
    service.served.stop()
    subprocess.run(["docker", "rm", "-f", server.name], capture_output=True)


def test_documents_search_by_words_and_meaning_and_erase_on_postgres(pg_docs):
    import psycopg

    service, url = pg_docs
    refunds = "Customers may return damaged goods within thirty days for a full refund. " * 12
    with service.client("alice", "pg") as alice:
        cid = alice.documents.ensure("tenants/pg/users/alice/kb/handbook", semantic=True)["id"]
        alice.documents.add(cid, "handbook.pdf", extraction=extraction(refunds, prose("shipping")))
        found = alice.documents.search(cid, "return damaged goods refund")
        assert found["note"] is None and "refund" in found["hits"][0]["snippet"].lower()
        assert alice.documents.reindex(cid) == 1
        assert alice.documents.search(cid, "shipping committee")["hits"]
    with psycopg.connect(url) as db:  # the derived index lives in its own schema, with pgvector columns
        tables = {r[0] for r in db.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='retrieval'")}
        assert {"library_documents", "library_sections", "vector_docs", "data_collections"} <= tables
        assert db.execute("SELECT count(*) FROM retrieval.vector_docs").fetchone()[0] > 0
    with service.admin("pg") as admin:
        receipt = admin.erasure.request("alice", tenant_id="pg")
        assert receipt["removed"]["libraries"]["documents"] == 1 and receipt["removed"]["vectors"] > 0
    with psycopg.connect(url) as db:
        assert db.execute("SELECT count(*) FROM retrieval.vector_docs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM retrieval.library_sections").fetchone()[0] == 0
