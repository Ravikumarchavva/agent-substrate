"""The PostgreSQL catalog: what differs from SQLite, and what makes several replicas safe."""

from __future__ import annotations

import subprocess
import threading

import pytest

from loamz.server.data.kernel.metadata import SCHEMA, CatalogStore
from loamz.server.data.kernel.metadata_backends import check_url, postgres_schema, to_postgres
from tests.data.conftest import start_postgres

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
        import time

        for _ in range(50):
            if b.acquire():
                break
            time.sleep(0.05)
        assert b.acquire() is True  # the server freed the lock, so the other replica leads
        assert a.acquire() is False  # the first notices, reconnects, and cannot take it back
    finally:
        a.release()
        b.release()
        first.close()
        second.close()


def test_conflicts_and_if_match_work_like_sqlite(store):
    from loamz.engine.errors import LoamzError, http_status

    schema = store.schema("main", "default")
    store.create_table(schema, {"name": "dup", "owner": "user:a"})
    with pytest.raises(LoamzError) as duplicate:
        store.create_table(schema, {"name": "dup", "owner": "user:a"})
    assert http_status(duplicate.value) == 409
    record = store.table("main", "default", "dup")
    with pytest.raises(LoamzError) as stale:
        store.update_securable("table", record, {"comment": "x"}, if_match=record["version"] + 5)
    assert http_status(stale.value) == 412
    store.update_securable("table", record, {"comment": "x"}, if_match=record["version"])
    store.update_securable("table", record, {"comment": "y"}, if_match=None)
    assert store.table("main", "default", "dup")["comment"] == "y"


def test_a_failed_transaction_leaves_nothing_behind(store):
    with pytest.raises(RuntimeError), store.connection() as db:
        db.execute(
            "INSERT INTO publications (job_id, kind, payload, state, created_at, updated_at) "
            "VALUES ('j', 'k', '{}', 'new', 'now', 'now')"
        )
        raise RuntimeError("boom")
    with store.connection() as db:
        assert db.execute("SELECT count(*) AS n FROM publications").fetchone()["n"] == 0


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


def test_the_postgres_schema_uses_wide_integers():
    ddl = "\n".join(postgres_schema(SCHEMA))
    assert "INTEGER" not in ddl.replace("BIGINT", "") and "AUTOINCREMENT" not in ddl
    assert "fts5" not in ddl and "PRAGMA" not in ddl  # search lives in the retrieval index now
    assert "DOUBLE PRECISION" in ddl
