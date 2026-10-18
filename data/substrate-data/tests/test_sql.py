"""One parse: read-only policy, table references, safe pruning filters, alias rewrite."""

from __future__ import annotations

import pytest
from pyiceberg.types import DateType, IntegerType, TimestampType

from substrate_data.core.errors import Problem
from substrate_data.sql.analysis import analyze
from substrate_data.tables.iceberg import coerce


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM read_parquet('s3://warehouse/x.parquet')",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT getenv('HOME')",
        "SELECT current_setting('s3_secret_access_key')",
        "SELECT * FROM duckdb_secrets()",
        "SELECT * FROM information_schema.tables",
        "SELECT * FROM pg_catalog.pg_tables",
        "DROP TABLE t",
        "INSERT INTO t VALUES (1)",
        "COPY (SELECT 1) TO '/tmp/x'",
        "ATTACH '/tmp/x.db'",
        "PRAGMA version",
        "SET threads = 1",
        "SELECT 1; SELECT 2",
        "INSTALL httpfs",
        "SELECT * FROM glob('/*')",
        "SELEC 1",
    ],
)
def test_policy_rejects(sql):
    with pytest.raises(Problem) as caught:
        analyze(sql, "main", "default")
    assert caught.value.status == 400


def test_references_defaults_ctes_and_aliases():
    analysis = analyze(
        "WITH recent AS (SELECT * FROM nyc.yellow WHERE fare > 10) "
        "SELECT r.a, z.b FROM recent r JOIN other.nyc.zones z ON z.id = r.zone, trips",
        "main",
        "default",
    )
    assert analysis.names == [
        ("main", "default", "trips"),
        ("main", "nyc", "yellow"),
        ("other", "nyc", "zones"),
    ]
    aliases = {id(ref.node): f"__t{i}" for i, ref in enumerate(analysis.refs)}
    sql = analysis.rewrite(aliases, limit=11)
    assert "nyc.yellow" not in sql and "zones" not in sql.replace("AS z", "")
    assert sql.endswith("LIMIT 11")
    assert "recent" in sql  # CTE names are left alone


def test_pruning_filters_are_null_rejecting_and_scoped():
    analysis = analyze(
        "SELECT * FROM t WHERE ts >= '2026-08-01' AND 5 < x AND y IN (1, 2) "
        "AND z BETWEEN 1 AND 3 AND (a = 1 OR b = 2) AND c IS NULL",
        "main",
        "default",
    )
    assert analysis.refs[0].filters == [
        ("ts", "ge", "2026-08-01"),
        ("x", "gt", 5),
        ("y", "in", [1, 2]),
        ("z", "ge", 1),
        ("z", "le", 3),
    ]


def test_no_pruning_through_outer_joins():
    analysis = analyze(
        "SELECT * FROM a LEFT JOIN b ON a.id = b.id WHERE b.x = 1 AND a.y = 2", "main", "default"
    )
    assert all(ref.filters == [] for ref in analysis.refs)


def test_join_filters_attach_to_the_right_table():
    analysis = analyze(
        "SELECT * FROM a JOIN b ON a.id = b.id WHERE b.x = 1 AND a.y > 2", "main", "default"
    )
    filters = {ref.name[-1]: ref.filters for ref in analysis.refs}
    assert filters == {"a": [("y", "gt", 2)], "b": [("x", "eq", 1)]}


def test_literal_coercion_for_iceberg_pruning():
    assert str(coerce("2026-08-01", TimestampType())) == "2026-08-01 00:00:00"
    assert str(coerce("2026-08-01 10:00", TimestampType())) == "2026-08-01 10:00:00"
    assert str(coerce("2026-08-01", DateType())) == "2026-08-01"
    assert coerce("yesterday", TimestampType()) is None
    assert coerce(2.5, IntegerType()) is None
