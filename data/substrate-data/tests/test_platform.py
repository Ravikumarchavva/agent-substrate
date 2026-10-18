"""End to end on real object storage and Iceberg: import, query, govern, transform, retrieve."""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import httpx
import polars as pl
import pytest
from substrate_data_client import DataError

from conftest import ADMIN

pytestmark = pytest.mark.integration


def trips(month: int, rows: int = 3000, extra: bool = False) -> pl.DataFrame:
    start = datetime(2026, month, 1)
    frame = pl.DataFrame(
        {
            "pickup": [start + timedelta(minutes=7 * i) for i in range(rows)],
            "zone": [i % 25 for i in range(rows)],
            "fare": [round(5 + (i % 97) * 0.75, 2) for i in range(rows)],
        }
    )
    # Nanosecond timestamps, as real TLC files have: ingest must normalize them.
    frame = frame.with_columns(pl.col("pickup").dt.cast_time_unit("ns"))
    return frame.with_columns(tip=pl.lit(1.5)) if extra else frame


def write(tmp_path, name: str, frame: pl.DataFrame) -> str:
    path = tmp_path / name
    frame.write_csv(path) if name.endswith(".csv") else frame.write_parquet(path)
    return str(path)


@pytest.fixture(scope="module")
def taxi(stack, tmp_path_factory):
    """main.nyc.trips: three months, partitioned by month."""
    folder = tmp_path_factory.mktemp("taxi")
    admin = stack.client("alice", *ADMIN)
    admin.create_schema("main", "nyc")
    files = [write(folder, f"m{m}.parquet", trips(m)) for m in (6, 7, 8)]
    record = admin.upload(
        "main.nyc.trips", files, partitioning=[{"column": "pickup", "transform": "month"}]
    )
    assert record["status"] == "succeeded" and record["rows"] == 9000
    return pl.concat([trips(m) for m in (6, 7, 8)])


def test_import_query_prune_preview_profile(stack, taxi):
    admin = stack.client("alice", *ADMIN)
    table = admin.table("main.nyc.trips")
    assert table["row_count"] == 9000 and table["file_count"] >= 3
    assert table["partitioning"] == [{"column": "pickup", "transform": "month"}]
    assert {c["name"]: c["type"] for c in table["columns"]}["pickup"] == "TIMESTAMP"

    result = admin.sql(
        "SELECT zone, count(*) AS n, sum(fare) AS revenue FROM trips "
        "WHERE pickup >= '2026-07-01' AND pickup < '2026-08-01' GROUP BY zone ORDER BY zone",
        schema="nyc",
    )
    expected = (
        taxi.filter(pl.col("pickup").dt.month() == 7)
        .group_by("zone")
        .agg(n=pl.len(), revenue=pl.col("fare").sum())
        .sort("zone")
    )
    assert result["n"].to_list() == expected["n"].to_list()
    assert result["revenue"].round(2).to_list() == expected["revenue"].round(2).to_list()

    with stack.http("alice", *ADMIN) as http:
        july = http.post(
            "/statements",
            json={
                "sql": "SELECT count(*) AS n FROM nyc.trips WHERE pickup >= '2026-07-01' AND pickup < '2026-08-01'"
            },
        ).json()
        everything = http.post(
            "/statements", json={"sql": "SELECT count(*) AS n FROM nyc.trips"}
        ).json()
        assert july["rows"] == [[3000]] and everything["rows"] == [[9000]]
        assert july["files_scanned"] < everything["files_scanned"]  # partition pruning
        preview = http.get(
            "/catalogs/main/schemas/nyc/tables/trips/preview",
            params={"limit": 5, "order_by": "fare", "descending": True},
        ).json()
        assert preview["total"] == 9000 and len(preview["rows"]) == 5
        assert preview["rows"][0][2] == taxi["fare"].max()
        profile = http.get("/catalogs/main/schemas/nyc/tables/trips/profile").json()
        fare = next(c for c in profile["columns"] if c["name"] == "fare")
        assert profile["rows_examined"] == 9000 and fare["max"] == taxi["fare"].max()
        bounded = http.post(
            "/statements", json={"sql": "SELECT * FROM nyc.trips", "max_rows": 10}
        ).json()
        assert bounded["row_count"] == 10 and bounded["truncated"] is True


def test_append_evolves_schema_and_overwrite_replaces(stack, taxi, tmp_path):
    admin = stack.client("alice", *ADMIN)
    admin.upload("main.nyc.evolving", [write(tmp_path, "a.parquet", trips(6, 100))])
    admin.upload(
        "main.nyc.evolving", [write(tmp_path, "b.parquet", trips(7, 50, extra=True))], mode="append"
    )
    frame = admin.sql("SELECT count(*) AS n, count(tip) AS tips FROM nyc.evolving")
    assert frame.row(0) == (150, 50)
    admin.upload("main.nyc.evolving", [write(tmp_path, "c.parquet", trips(8, 7))], mode="overwrite")
    assert admin.sql("SELECT count(*) AS n FROM nyc.evolving").item() == 7
    assert admin.table("main.nyc.evolving")["row_count"] == 7


def test_multifile_csv_import(stack, tmp_path):
    admin = stack.client("alice", *ADMIN)
    files = [write(tmp_path, f"part{m}.csv", trips(m, 500)) for m in (1, 2)]
    record = admin.upload("main.nyc.from_csv", files)
    assert record["rows"] == 1000
    assert admin.sql("SELECT count(DISTINCT zone) AS z FROM nyc.from_csv").item() == 25


def test_governance_for_an_analyst(stack, taxi):
    bob = stack.client("bob", "analysts")
    with pytest.raises(DataError) as caught:
        bob.sql("SELECT count(*) FROM main.nyc.trips")
    assert caught.value.status == 404  # existence is not disclosed
    with stack.http("alice", *ADMIN) as admin:
        changed = admin.patch(
            "/grants/schema/main.nyc",
            json={"changes": [{"principal": "group:analysts", "add": ["USE_SCHEMA", "SELECT"]}]},
        )
        assert changed.status_code == 200, changed.text
    assert bob.sql("SELECT count(*) AS n FROM main.nyc.trips").item() == 9000
    with pytest.raises(DataError) as caught:
        bob.request(
            "POST",
            "/imports",
            json={
                "table": "main.nyc.trips",
                "mode": "append",
                "files": [{"name": "x.csv", "size": 1}],
            },
        )
    assert caught.value.status == 403
    with stack.http("alice", *ADMIN) as admin:
        admin.patch(
            "/grants/schema/main.nyc",
            json={"changes": [{"principal": "group:analysts", "remove": ["SELECT"]}]},
        )
        events = admin.get("/audit-events").json()["events"]
        assert any(
            e["action"] == "grants.update" and e["principal"] == "user:alice" for e in events
        )
    with pytest.raises(DataError) as caught:
        bob.sql("SELECT count(*) FROM main.nyc.trips")
    assert caught.value.status == 404  # no privilege left: the table is hidden again
    history = bob.request("GET", "/statements")["statements"]
    assert [s["status"] for s in history[:2]] == ["denied", "succeeded"]


def test_pipeline_writes_a_governed_table(stack, taxi):
    with stack.http("alice", *ADMIN) as http:
        pipeline = http.post(
            "/pipelines",
            json={
                "name": "expensive",
                "source": "main.nyc.trips",
                "target": "main.nyc.expensive_trips",
                "definition": {
                    "columns": ["zone", "fare"],
                    "filter_column": "fare",
                    "operator": "gt",
                    "value": 70,
                },
            },
        ).json()
        run = http.post(f"/pipelines/{pipeline['id']}:run").json()
        deadline = time.monotonic() + 120
        while run["status"] not in {"succeeded", "failed"} and time.monotonic() < deadline:
            time.sleep(0.5)
            run = next(r for r in http.get("/pipelines").json()["runs"] if r["id"] == run["id"])
        assert run["status"] == "succeeded", run
    admin = stack.client("alice", *ADMIN)
    assert (
        admin.sql("SELECT count(*) FROM nyc.expensive_trips").item()
        == taxi.filter(pl.col("fare") > 70).height
    )
    table = admin.table("main.nyc.expensive_trips")
    assert [c["name"] for c in table["columns"]] == ["zone", "fare"]


def test_partition_folders_never_become_columns(stack, taxi):
    """Files live under `__p0=<value>/` folders; engines must not turn those names into data."""
    admin = stack.client("alice", *ADMIN)
    frame = admin.sql("SELECT * FROM nyc.trips LIMIT 1")
    assert frame.columns == ["pickup", "zone", "fare"]
    with stack.http("alice", *ADMIN) as http:
        pipeline = http.post(
            "/pipelines",
            json={"name": "copy_all", "source": "main.nyc.trips", "target": "main.nyc.copy_all"},
        ).json()
        run = http.post(f"/pipelines/{pipeline['id']}:run").json()
        deadline = time.monotonic() + 120
        while run["status"] not in {"succeeded", "failed"} and time.monotonic() < deadline:
            time.sleep(0.5)
            run = next(r for r in http.get("/pipelines").json()["runs"] if r["id"] == run["id"])
        assert run["status"] == "succeeded", run
    assert admin.sql("SELECT * FROM nyc.copy_all LIMIT 1").columns == ["pickup", "zone", "fare"]
    assert admin.sql("SELECT count(*) FROM nyc.copy_all").item() == 9000


def test_statement_timeout_and_errors(stack, taxi):
    with stack.http("alice", *ADMIN) as http:
        slow = http.post(
            "/statements",
            json={
                "sql": "SELECT count(*) FROM nyc.trips a, nyc.trips b, nyc.trips c",
                "timeout_seconds": 0.5,
            },
        )
        assert slow.status_code == 400 and slow.json()["type"].endswith("statement-timeout")
        bad = http.post("/statements", json={"sql": "SELECT nope FROM nyc.trips"})
        assert bad.status_code == 400 and bad.headers["content-type"] == "application/problem+json"
        assert bad.json()["request_id"] == bad.headers["x-request-id"]


def test_api_contract(stack):
    with stack.http(None) as anonymous:
        missing = anonymous.get("/catalogs")
        assert missing.status_code == 401 and "Bearer" in missing.headers["www-authenticate"]
        assert missing.json()["type"].endswith("/unauthorized")
    assert httpx.get(stack.api + "/api/v1/openapi.json").json()["info"]["version"] == "1.0.0"
    assert httpx.get(stack.api + "/api/datasets").status_code == 404  # unversioned routes are gone
    with stack.http("alice", *ADMIN) as http:
        first = http.get("/catalogs", params={"page_size": 1}).json()
        assert len(first["catalogs"]) == 1
        schema = http.get("/catalogs/main/schemas/default")
        stale = http.patch(
            "/catalogs/main/schemas/default",
            json={"comment": "x"},
            headers={"If-Match": 'W/"999"'},
        )
        assert stale.status_code == 412
        fresh = http.patch(
            "/catalogs/main/schemas/default",
            json={"comment": "Default schema"},
            headers={"If-Match": schema.headers["etag"]},
        )
        assert fresh.status_code == 200 and fresh.json()["comment"] == "Default schema"


def test_documents_are_chunked_on_compute_and_searchable(stack):
    with stack.http("alice", *ADMIN) as http:
        collection = http.post("/collections", json={"name": "handbook"}).json()
        http.post(
            f"/collections/{collection['id']}/documents",
            json={"title": "Storage", "text": "Iceberg tables live in SeaweedFS object storage."},
        )
        deadline = time.monotonic() + 60
        results = []
        while not results and time.monotonic() < deadline:
            time.sleep(0.3)
            results = http.get(
                f"/collections/{collection['id']}/search", params={"q": "iceberg"}
            ).json()["results"]
        assert results and results[0]["title"] == "Storage"
    with stack.http("bob", "analysts") as other:
        assert (
            other.get(f"/collections/{collection['id']}/search", params={"q": "x"}).status_code
            == 404
        )


# ------------------------------------------------------------------ views
@pytest.fixture(scope="module")
def sales(stack, tmp_path_factory):
    """main.sales.orders (admin-only) and the schema around it."""
    folder = tmp_path_factory.mktemp("sales")
    admin = stack.client("alice", *ADMIN)
    admin.create_schema("main", "sales")
    frame = pl.DataFrame(
        {
            "region": ["north", "south", "north", "east"] * 250,
            "amount": [float(i % 40) for i in range(1000)],
            "customer": [f"c{i}" for i in range(1000)],
        }
    )
    admin.upload("main.sales.orders", [write(folder, "orders.parquet", frame)])
    with stack.http("alice", *ADMIN) as http:
        http.patch(
            "/grants/schema/main.sales",
            json={"changes": [{"principal": "group:account users", "add": ["USE_SCHEMA"]}]},
        )
    return frame


def test_views_share_a_slice_without_exposing_the_table(stack, sales):
    admin, bob = stack.client("alice", *ADMIN), stack.client("bob", "analysts")
    created = admin.create_view(
        "main.sales.revenue_by_region",
        "SELECT region, count(*) AS orders, sum(amount) AS revenue FROM main.sales.orders GROUP BY region",
        comment="No customer data",
    )
    assert [c["name"] for c in created["columns"]] == ["region", "orders", "revenue"]
    assert (
        created["owner"] == "user:alice" and created["full_name"] == "main.sales.revenue_by_region"
    )
    # Bob sees neither table nor view until granted the view only.
    with pytest.raises(DataError) as caught:
        bob.sql("SELECT * FROM main.sales.revenue_by_region")
    assert caught.value.status == 404
    with stack.http("alice", *ADMIN) as http:
        granted = http.patch(
            "/grants/view/main.sales.revenue_by_region",
            json={"changes": [{"principal": "group:analysts", "add": ["SELECT"]}]},
        )
        assert granted.status_code == 200, granted.text
    result = bob.sql("SELECT region, revenue FROM main.sales.revenue_by_region ORDER BY region")
    expected = sales.group_by("region").agg(revenue=pl.col("amount").sum()).sort("region")
    assert result["revenue"].to_list() == expected["revenue"].to_list()
    # The base table stays closed, including through a join or a subquery smuggled around the view.
    for sql in [
        "SELECT * FROM main.sales.orders",
        "SELECT customer FROM main.sales.orders LIMIT 1",
    ]:
        with pytest.raises(DataError) as caught:
            bob.sql(sql)
        assert caught.value.status == 404
    history = bob.request("GET", "/statements")["statements"]
    assert any("main.sales.revenue_by_region" in s["tables"] for s in history)
    listed = bob.request("GET", "/catalogs/main/schemas/sales/views")["views"]
    assert [v["name"] for v in listed] == ["revenue_by_region"]
    assert bob.request("GET", "/catalogs/main/schemas/sales/tables")["tables"] == []


def test_views_compose_filter_and_join_like_tables(stack, sales):
    admin = stack.client("alice", *ADMIN)
    admin.create_view("main.sales.north", "SELECT * FROM main.sales.orders WHERE region = 'north'")
    admin.create_view("main.sales.north_big", "SELECT * FROM main.sales.north WHERE amount >= 30")
    count = admin.sql("SELECT count(*) AS n FROM sales.north_big").item()
    assert count == sales.filter((pl.col("region") == "north") & (pl.col("amount") >= 30)).height
    joined = admin.sql(
        "SELECT n.region, count(*) AS n FROM sales.north n JOIN sales.orders o "
        "ON n.customer = o.customer GROUP BY 1"
    )
    assert joined.row(0) == ("north", 500)
    preview = admin.request(
        "GET", "/catalogs/main/schemas/sales/views/north/preview", params={"limit": 3}
    )
    assert preview["row_count"] == 3 and preview["columns"][0]["name"] == "region"
    # Pruning still happens inside the view definition.
    with stack.http("alice", *ADMIN) as http:
        one = http.post("/statements", json={"sql": "SELECT count(*) FROM sales.north"}).json()
        assert one["rows"] == [[500]]


def test_view_definition_is_validated_and_authorized_at_creation(stack, sales):
    bob = stack.client("bob", "analysts")
    admin = stack.client("alice", *ADMIN)
    for sql in [
        "DROP TABLE main.sales.orders",
        "SELECT * FROM read_csv('/etc/passwd')",
        "SELECT 1; SELECT 2",
    ]:
        with pytest.raises(DataError) as caught:
            admin.create_view("main.sales.bad", sql)
        assert caught.value.status == 400
    with pytest.raises(DataError) as caught:
        admin.create_view("main.sales.bad", "SELECT * FROM main.sales.missing")
    assert caught.value.status == 404
    with stack.http("alice", *ADMIN) as http:
        http.patch(
            "/grants/schema/main.sales",
            json={"changes": [{"principal": "group:analysts", "add": ["CREATE_TABLE"]}]},
        )
    # Bob may create objects in the schema but cannot define a view over data he cannot read.
    with pytest.raises(DataError) as caught:
        bob.create_view("main.sales.sneaky", "SELECT customer FROM main.sales.orders")
    assert caught.value.status == 404
    with pytest.raises(DataError) as caught:
        admin.create_view("main.sales.orders", "SELECT 1 AS x")  # a table already has that name
    assert caught.value.status == 409


def test_views_follow_their_owner_and_can_be_dropped(stack, sales):
    admin = stack.client("alice", *ADMIN)
    bob = stack.client("bob", "analysts")
    bob_is_owner = bob.create_view("main.sales.bobs_view", "SELECT 1 AS one")
    assert bob_is_owner["owner"] == "user:bob"
    with stack.http("alice", *ADMIN) as http:
        # Bob owns the view but is not allowed to widen it to the table he cannot read.
        assert http.delete("/catalogs/main/schemas/sales/views/bobs_view").status_code == 204
        assert http.get("/catalogs/main/schemas/sales/views/bobs_view").status_code == 404
        # Dropping a view does not touch the table; a schema with views is not empty.
        assert http.delete("/catalogs/main/schemas/sales").status_code == 409
    assert admin.sql("SELECT count(*) AS n FROM sales.orders").item() == 1000


def test_a_view_over_a_dropped_table_fails_clearly(stack, tmp_path):
    admin = stack.client("alice", *ADMIN)
    admin.create_schema("main", "scratch")
    admin.upload(
        "main.scratch.base", [write(tmp_path, "b.parquet", pl.DataFrame({"x": [1, 2, 3]}))]
    )
    admin.create_view("main.scratch.v", "SELECT sum(x) AS total FROM main.scratch.base")
    assert admin.sql("SELECT total FROM scratch.v").item() == 6
    with stack.http("alice", *ADMIN) as http:
        assert http.delete("/catalogs/main/schemas/scratch/tables/base").status_code == 204
        broken = http.post("/statements", json={"sql": "SELECT * FROM scratch.v"})
    assert broken.status_code == 409 and broken.json()["type"].endswith("view-broken")
