"""The PostgreSQL catalog: what differs from SQLite, and what makes several replicas safe."""

from __future__ import annotations

import subprocess
import threading

import pytest

from conftest import start_postgres
from substrate_data.catalog.backends import check_url, postgres_schema, to_postgres
from substrate_data.catalog.store import SCHEMA, CatalogStore

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def server():
    server = start_postgres()
    yield server
    subprocess.run(["docker", "rm", "-f", server.name], capture_output=True)


@pytest.fixture
def store(server):
    store = CatalogStore(url=server.database())
    store.ensure_default("data-admins")
    yield store
    store.close()


def test_counters_hold_billions_of_rows_and_terabytes(store):
    schema = store.schema("main", "default")
    table = store.create_table(schema, {"name": "huge", "owner": "user:a"})
    store.update_table_state(
        table["id"], {"row_count": 3_000_000_000, "size_bytes": 40 * 1024**4, "file_count": 160}
    )
    loaded = store.table("main", "default", "huge")
    assert loaded["row_count"] == 3_000_000_000 and loaded["size_bytes"] == 40 * 1024**4


def test_search_ignores_case_like_sqlite(store):
    schema = store.schema("main", "default")
    store.create_table(schema, {"name": "q3", "owner": "user:a", "comment": "Quarterly REVENUE"})
    assert [r["name"] for _, r in store.search("revenue", 10)] == ["q3"]
    assert store.search("100%_literal", 10) == []  # wildcards in the search text are literal


def test_full_text_search_ranks_and_survives_hostile_input(store):
    store.add_chunks("c1", "d1", "Doc one", ["the quick brown fox", "lazy dogs sleep"])
    store.add_chunks("c1", "d2", "Doc two", ["fox fox fox hunting in the forest"])
    store.add_chunks("c2", "d3", "Other", ["fox in another collection"])
    hits = store.search_chunks("c1", ["fox"], 10)
    assert {h["document_id"] for h in hits} == {"d1", "d2"}  # collections stay separate
    assert hits[0]["document_id"] == "d2"  # more occurrences rank first (higher is better)
    assert store.search_chunks("c1", ['"); DROP TABLE chunks; --', "&|!:*"], 5) == []
    store.add_chunks("c1", "d1", "Doc one", ["replaced"])  # re-adding replaces the document
    assert [h["text"] for h in store.list_chunks("c1", 10) if h["document_id"] == "d1"] == [
        "replaced"
    ]


def test_replicas_starting_together_create_one_default_namespace(server):
    url = server.database()
    errors, stores = [], []

    def start():
        try:
            replica = CatalogStore(url=url)
            replica.ensure_default("data-admins")
            stores.append(replica)
        except Exception as exc:  # noqa: BLE001 - the test reports whatever went wrong
            errors.append(exc)

    threads = [threading.Thread(target=start) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    with stores[0].connection() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM catalogs WHERE name='main'").fetchone()["n"] == 1
        )
    [s.close() for s in stores]


def test_only_one_replica_publishes_and_another_takes_over_when_it_dies(server):
    url = server.database()
    first, second = CatalogStore(url=url), CatalogStore(url=url)
    a, b = first.leader("publisher"), second.leader("publisher")
    try:
        assert a.acquire() is True and a.acquire() is True  # holding it is stable
        assert b.acquire() is False
        a.conn.close()  # the leader's connection dies, as when its process is killed
        assert b.acquire() is True  # the server freed the lock, so the other replica leads
        assert a.acquire() is False  # the first notices, reconnects, and cannot take it back
    finally:
        a.release()
        b.release()
        first.close()
        second.close()


def test_conflicts_and_if_match_work_like_sqlite(store):
    from substrate_data.core.errors import Problem

    schema = store.schema("main", "default")
    store.create_table(schema, {"name": "dup", "owner": "user:a"})
    with pytest.raises(Problem) as duplicate:
        store.create_table(schema, {"name": "dup", "owner": "user:a"})
    assert duplicate.value.status == 409
    record = store.table("main", "default", "dup")
    with pytest.raises(Problem) as stale:
        store.update_securable("table", record, {"comment": "x"}, if_match=record["version"] + 5)
    assert stale.value.status == 412
    store.update_securable("table", record, {"comment": "x"}, if_match=record["version"])
    store.update_securable("table", record, {"comment": "y"}, if_match=None)
    assert store.table("main", "default", "dup")["comment"] == "y"


def test_a_failed_transaction_leaves_nothing_behind(store):
    with pytest.raises(RuntimeError), store.connection() as db:
        db.execute(
            "INSERT INTO collections (id, name, owner, created_at) VALUES ('c', 'n', 'o', 'now')"
        )
        raise RuntimeError("boom")
    with store.connection() as db:
        assert db.execute("SELECT count(*) AS n FROM collections").fetchone()["n"] == 0


def test_schema_setup_is_idempotent_and_safe_to_run_concurrently(server):
    url = server.database()
    errors = []

    def open_store():
        try:
            CatalogStore(url=url).close()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=open_store) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
    CatalogStore(url=url).close()  # and again, on top of the existing schema


def test_a_remote_database_must_be_encrypted():
    with pytest.raises(ValueError, match="sslmode"):
        check_url("postgresql://u:p@db.example.com/catalog")
    with pytest.raises(ValueError, match="sslmode"):
        check_url("postgresql://u:p@db.example.com/catalog?sslmode=disable")
    check_url("postgresql://u:p@db.example.com/catalog?sslmode=verify-full")
    check_url("postgresql://u:p@127.0.0.1/catalog")  # same machine, development
    check_url("postgresql://u:p@/catalog?host=/var/run/postgresql")


def test_sql_is_translated_without_touching_literals():
    assert to_postgres("SELECT * FROM t WHERE a=? AND b='x?y'") == (
        "SELECT * FROM t WHERE a=%s AND b='x?y'"
    )
    assert "ILIKE" in to_postgres("SELECT 1 WHERE name LIKE ? ESCAPE '\\'")
    assert to_postgres("INSERT OR IGNORE INTO g VALUES (?,?)") == (
        "INSERT INTO g VALUES (%s,%s) ON CONFLICT DO NOTHING"
    )
    assert to_postgres("SELECT '100%' AS p") == "SELECT '100%%' AS p"


def test_the_postgres_schema_uses_wide_integers_and_native_search():
    ddl = "\n".join(postgres_schema(SCHEMA))
    assert "INTEGER" not in ddl.replace("BIGINT", "") and "AUTOINCREMENT" not in ddl
    assert "tsvector" in ddl and "fts5" not in ddl and "PRAGMA" not in ddl
    assert "DOUBLE PRECISION" in ddl
