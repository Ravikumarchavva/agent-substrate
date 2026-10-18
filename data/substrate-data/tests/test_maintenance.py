"""Table maintenance, materialized views and the worker result cache, against the real stack."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import polars as pl
import pytest
from substrate_data_client import DataError

from conftest import ADMIN
from substrate_data.compute import batch
from substrate_data.core.errors import Problem

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def admin(stack):
    client = stack.client("alice", *ADMIN)
    client.create_schema("main", "m3")
    with stack.http("alice", *ADMIN) as http:
        http.patch(
            "/grants/schema/main.m3",
            json={"changes": [{"principal": "group:account users", "add": ["USE_SCHEMA"]}]},
        )
    return client


def scattered(directory, name, rows=500, seed=0, start=0):
    """A parquet file whose `k` values span the whole range, like data appended in arrival order."""
    frame = pl.DataFrame(
        {
            "k": pl.Series(range(start, start + rows * 6)).sample(rows, seed=seed),
            "region": ["north", "south", "east"] * (rows // 3) + ["west"] * (rows % 3),
            "amount": [float(i % 17) for i in range(rows)],
        }
    )
    path = directory / f"{name}.parquet"
    frame.write_parquet(path)
    return path


def make_table(admin, tmp_path, name, files=6, **options):
    admin.upload(f"main.m3.{name}", [scattered(tmp_path, f"{name}-0", seed=0)], **options)
    for index in range(1, files):
        admin.upload(
            f"main.m3.{name}",
            [scattered(tmp_path, f"{name}-{index}", seed=index)],
            mode="append",
        )


def wait_for(fetch, done, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fetch()
        if done(value):
            return value
        time.sleep(0.3)
    raise AssertionError(f"timed out; last value: {value}")


def run_optimize(stack, name, body, expect=202):
    with stack.http("alice", *ADMIN) as http:
        reply = http.post(f"/catalogs/main/schemas/m3/tables/{name}:optimize", json=body)
        assert reply.status_code == expect, reply.text
        run = reply.json()
        if reply.status_code != 202 or run["status"] == "skipped":
            return run

        def fetch():
            return http.get(
                f"/catalogs/main/schemas/m3/tables/{name}/maintenance-runs/{run['id']}"
            ).json()

        return wait_for(fetch, lambda r: r["status"] in {"succeeded", "failed"})


def table(admin, name):
    return admin.table(f"main.m3.{name}")


# ------------------------------------------------------------------ compaction and clustering
def test_compaction_merges_small_files_and_changes_no_answer(stack, admin, tmp_path):
    make_table(admin, tmp_path, "compact_me")
    before = table(admin, "compact_me")
    assert before["file_count"] == 6 and before["row_count"] == 3000
    answer = admin.sql(
        "SELECT region, count(*) AS n, sum(k) AS s FROM main.m3.compact_me GROUP BY 1 ORDER BY 1"
    )

    run = run_optimize(stack, "compact_me", {"strategy": "compact"})
    assert run["status"] == "succeeded", run
    assert run["files_in"] == 6 and run["files_out"] == 1 and run["rows"] == 3000

    after = table(admin, "compact_me")
    assert after["file_count"] == 1 and after["row_count"] == 3000
    assert after["snapshot_id"] != before["snapshot_id"]
    assert admin.sql(
        "SELECT region, count(*) AS n, sum(k) AS s FROM main.m3.compact_me GROUP BY 1 ORDER BY 1"
    ).equals(answer)


def test_an_already_compact_table_is_skipped_not_rewritten(stack, admin, tmp_path):
    make_table(admin, tmp_path, "tidy", files=1)
    run = run_optimize(stack, "tidy", {"strategy": "compact"})
    assert run["status"] == "skipped"
    assert table(admin, "tidy")["file_count"] == 1


def test_sorting_orders_the_rows_so_files_get_tight_statistics(stack, admin, tmp_path):
    make_table(admin, tmp_path, "sort_me")
    unsorted = admin.sql("SELECT k FROM main.m3.sort_me")["k"].to_list()
    assert unsorted != sorted(unsorted)  # arrival order scattered the keys

    narrow_sql = "SELECT count(*) FROM main.m3.sort_me WHERE k < 100"
    scattered_scan = admin.request("POST", "/statements", json={"sql": narrow_sql})["files_scanned"]
    assert scattered_scan == 6  # every file's range overlaps the filter

    run = run_optimize(stack, "sort_me", {"strategy": "sort", "sort_by": ["k"]})
    assert run["status"] == "succeeded", run
    result = admin.sql("SELECT k FROM main.m3.sort_me")["k"].to_list()
    assert result == sorted(unsorted)
    # Sorted and merged, the same filter is answered from a single file.
    narrow = admin.request("POST", "/statements", json={"sql": narrow_sql})
    assert narrow["files_scanned"] == 1


def test_partitioned_tables_keep_their_partitions_and_can_be_optimized_in_part(
    stack, admin, tmp_path
):
    months = [datetime(2026, m, d) for m in (1, 2, 3) for d in (1, 9, 17, 25)]
    for index in range(2):
        path = tmp_path / f"events-{index}.parquet"
        pl.DataFrame({"ts": months, "k": list(range(len(months)))}).write_parquet(path)
        options = (
            {"partitioning": [{"column": "ts", "transform": "month"}]}
            if index == 0
            else {"mode": "append"}
        )
        admin.upload("main.m3.by_month", [path], **options)
    assert table(admin, "by_month")["file_count"] == 6  # 3 months x 2 appends

    run = run_optimize(
        stack,
        "by_month",
        {"strategy": "compact", "where": [{"column": "ts", "op": "ge", "value": "2026-03-01"}]},
    )
    assert run["status"] == "succeeded", run
    assert run["files_in"] == 2  # only March's two files were touched
    after = table(admin, "by_month")
    assert after["file_count"] == 5 and after["row_count"] == 24
    counts = admin.sql(
        "SELECT month(ts) AS m, count(*) AS n FROM main.m3.by_month GROUP BY 1 ORDER BY 1"
    )
    assert counts["n"].to_list() == [8, 8, 8]


def test_a_rewrite_never_loses_rows_appended_while_it_ran(stack, admin, tmp_path):
    """The commit swaps only the files it planned, so a concurrent append survives."""
    make_table(admin, tmp_path, "racy", files=3)
    services = stack.app.state.services
    record = services.store.table("main", "m3", "racy")
    planned = services.tables.plan(record, [])
    old = [f.path for f in planned.files]
    write_id = uuid4().hex
    result = batch.optimize(
        {
            "source_files": old,
            "sort_by": [],
            "location": services.tables.load(record).location(),
            "write_id": write_id,
            "partitioning": [],
            "target_file_bytes": 256 * 2**20,
        },
        stack.worker_settings,
    )
    admin.upload(
        "main.m3.racy", [scattered(tmp_path, "late", seed=99, rows=100)], mode="append"
    )  # lands after planning, before the commit

    record = services.store.table("main", "m3", "racy")
    committed = services.tables.replace_files(
        record, old, [f["path"] for f in result["files"]], write_id
    )
    stack.app.state.publisher.refresh(record, committed)
    assert admin.sql("SELECT count(*) AS n FROM main.m3.racy")["n"][0] == 1500 + 100
    assert table(admin, "racy")["file_count"] == 2  # the rewrite plus the late append

    # Replaying the same commit is a no-op; a different rewrite of files that are gone is refused.
    services.tables.replace_files(record, old, [f["path"] for f in result["files"]], write_id)
    assert admin.sql("SELECT count(*) AS n FROM main.m3.racy")["n"][0] == 1600
    with pytest.raises(Problem) as refused:
        services.tables.replace_files(
            record, old, [f["path"] for f in result["files"]], uuid4().hex
        )
    assert refused.value.status == 409 and refused.value.code == "table-changed"


def test_maintenance_needs_modify_validates_columns_and_runs_one_at_a_time(stack, admin, tmp_path):
    make_table(admin, tmp_path, "guarded")
    with stack.http("bob", "analysts") as http:
        assert (
            http.post("/catalogs/main/schemas/m3/tables/guarded:optimize", json={}).status_code
            == 404
        )  # bob cannot even see the table
    with stack.http("alice", *ADMIN) as http:
        http.patch(
            "/grants/table/main.m3.guarded",
            json={"changes": [{"principal": "group:analysts", "add": ["SELECT"]}]},
        )
    with stack.http("bob", "analysts") as http:
        forbidden = http.post("/catalogs/main/schemas/m3/tables/guarded:optimize", json={})
        assert forbidden.status_code == 403
        assert (
            http.get("/catalogs/main/schemas/m3/tables/guarded/maintenance-runs").status_code == 403
        )
    with stack.http("alice", *ADMIN) as http:
        assert (
            http.post(
                "/catalogs/main/schemas/m3/tables/guarded:optimize",
                json={"strategy": "sort", "sort_by": ["nope"]},
            ).status_code
            == 400
        )
        assert (
            http.post(
                "/catalogs/main/schemas/m3/tables/guarded:optimize", json={"strategy": "sort"}
            ).status_code
            == 400
        )
        first = http.post("/catalogs/main/schemas/m3/tables/guarded:optimize", json={})
        assert first.status_code == 202
        second = http.post("/catalogs/main/schemas/m3/tables/guarded:optimize", json={})
        assert second.status_code == 409
        wait_for(
            lambda: http.get(
                f"/catalogs/main/schemas/m3/tables/guarded/maintenance-runs/{first.json()['id']}"
            ).json(),
            lambda r: r["status"] in {"succeeded", "failed"},
        )
        runs = http.get("/catalogs/main/schemas/m3/tables/guarded/maintenance-runs").json()["runs"]
        assert [r["status"] for r in runs][0] == "succeeded"


# ------------------------------------------------------------------ materialized views
def create_mv(stack, name, sql, user="alice", groups=ADMIN):
    with stack.http(user, *groups) as http:
        reply = http.post(
            "/catalogs/main/schemas/m3/materialized-views", json={"name": name, "sql": sql}
        )
        assert reply.status_code == 202, reply.text
        return reply.json()


def wait_fresh(stack, name, user="alice", groups=ADMIN):
    with stack.http(user, *groups) as http:
        return wait_for(
            lambda: http.get(f"/catalogs/main/schemas/m3/tables/{name}").json(),
            lambda t: t["materialized"]["status"] in {"fresh", "failed"},
        )


def test_a_materialized_view_is_computed_once_then_read_like_a_table(stack, admin, tmp_path):
    make_table(admin, tmp_path, "sales", files=2)
    created = create_mv(
        stack,
        "sales_by_region",
        "SELECT region, count(*) AS orders, sum(amount) AS revenue, max(k) AS last_k "
        "FROM main.m3.sales GROUP BY region",
    )
    assert created["table"] == "main.m3.sales_by_region"
    view = wait_fresh(stack, "sales_by_region")
    assert view["materialized"]["status"] == "fresh" and view["materialized"]["stale"] is False
    assert view["materialized"]["sources"] == ["main.m3.sales"]
    assert view["row_count"] == 4

    expected = admin.sql(
        "SELECT region, count(*) AS orders, sum(amount) AS revenue, max(k) AS last_k "
        "FROM main.m3.sales GROUP BY region ORDER BY region"
    )
    stored = admin.sql("SELECT * FROM main.m3.sales_by_region ORDER BY region")
    assert stored.equals(expected)
    assert stored.schema == expected.schema  # types survive the round trip through Iceberg

    # Reading it prunes and scans files like any table: no recomputation happens.
    meta = admin.request(
        "POST", "/statements", json={"sql": "SELECT * FROM main.m3.sales_by_region"}
    )
    assert meta["files_scanned"] == 1


def test_a_view_becomes_stale_when_its_sources_change_and_refresh_catches_up(
    stack, admin, tmp_path
):
    make_table(admin, tmp_path, "ledger", files=1)
    create_mv(
        stack, "ledger_total", "SELECT count(*) AS n, sum(amount) AS total FROM main.m3.ledger"
    )
    assert wait_fresh(stack, "ledger_total")["materialized"]["stale"] is False
    assert admin.sql("SELECT n FROM main.m3.ledger_total")["n"][0] == 500

    admin.upload("main.m3.ledger", [scattered(tmp_path, "more", seed=7, rows=100)], mode="append")
    view = table(admin, "ledger_total")
    assert view["materialized"]["stale"] is True  # honest about being behind
    assert admin.sql("SELECT n FROM main.m3.ledger_total")["n"][0] == 500  # still the old answer

    with stack.http("alice", *ADMIN) as http:
        reply = http.post("/catalogs/main/schemas/m3/tables/ledger_total:refresh")
        assert reply.status_code == 202, reply.text
        wait_for(
            lambda: http.get(
                f"/catalogs/main/schemas/m3/tables/ledger_total/maintenance-runs/{reply.json()['id']}"
            ).json(),
            lambda r: r["status"] in {"succeeded", "failed"},
        )
    view = table(admin, "ledger_total")
    assert view["materialized"]["stale"] is False
    assert admin.sql("SELECT n FROM main.m3.ledger_total")["n"][0] == 600


def test_readers_need_select_on_the_view_only_and_cannot_write_to_it(stack, admin, tmp_path):
    make_table(admin, tmp_path, "secret", files=1)
    create_mv(
        stack,
        "secret_totals",
        "SELECT region, sum(amount) AS total FROM main.m3.secret GROUP BY region",
    )
    wait_fresh(stack, "secret_totals")
    with stack.http("alice", *ADMIN) as http:
        http.patch(
            "/grants/table/main.m3.secret_totals",
            json={"changes": [{"principal": "group:readers", "add": ["SELECT"]}]},
        )
    reader = stack.client("rita", "readers")
    assert len(reader.sql("SELECT * FROM main.m3.secret_totals")) == 4
    with pytest.raises(DataError) as hidden:
        reader.sql("SELECT * FROM main.m3.secret")
    assert hidden.value.status == 404  # the source table stays invisible

    with stack.http("rita", "readers") as http:
        assert (
            http.post("/catalogs/main/schemas/m3/tables/secret_totals:refresh").status_code == 403
        )
        assert http.post(
            "/catalogs/main/schemas/m3/tables/secret_totals/maintenance-runs"
        ).status_code in {404, 405}
    with pytest.raises(DataError) as blocked:
        admin.upload("main.m3.secret_totals", [scattered(tmp_path, "x", rows=3)], mode="append")
    assert blocked.value.status == 409


def test_refresh_runs_as_the_definer_and_stops_if_they_lose_access(stack, admin, tmp_path):
    make_table(admin, tmp_path, "payroll", files=1)
    with stack.http("alice", *ADMIN) as http:
        http.patch(
            "/grants/table/main.m3.payroll",
            json={"changes": [{"principal": "group:finance", "add": ["SELECT"]}]},
        )
        http.patch(
            "/grants/schema/main.m3",
            json={"changes": [{"principal": "group:finance", "add": ["CREATE_TABLE"]}]},
        )
    create_mv(
        stack,
        "payroll_totals",
        "SELECT region, sum(amount) AS total FROM main.m3.payroll GROUP BY 1",
        "carol",
        ("finance",),
    )
    assert (
        wait_fresh(stack, "payroll_totals", "carol", ("finance",))["materialized"]["status"]
        == "fresh"
    )

    with stack.http("alice", *ADMIN) as http:
        http.patch(
            "/grants/table/main.m3.payroll",
            json={"changes": [{"principal": "group:finance", "remove": ["SELECT"]}]},
        )
        refused = http.post("/catalogs/main/schemas/m3/tables/payroll_totals:refresh")
        assert refused.status_code == 403
        assert refused.json()["type"].endswith("materialized-view-definer-access")


def test_a_definition_the_creator_cannot_read_is_refused_up_front(stack, admin, tmp_path):
    make_table(admin, tmp_path, "locked", files=1)
    with stack.http("dave", "devs") as http:
        reply = http.post(
            "/catalogs/main/schemas/m3/materialized-views",
            json={"name": "peek", "sql": "SELECT * FROM main.m3.locked"},
        )
        assert reply.status_code in {403, 404}
    with pytest.raises(DataError):
        admin.table("main.m3.peek")


def test_a_failed_refresh_is_reported_and_leaves_the_old_rows(stack, admin, tmp_path):
    make_table(admin, tmp_path, "fragile", files=1)
    create_mv(stack, "fragile_sum", "SELECT sum(amount) AS total FROM main.m3.fragile")
    wait_fresh(stack, "fragile_sum")
    admin.sql("SELECT 1")
    # Dropping the source breaks the definition; the view keeps serving its last good answer.
    with stack.http("alice", *ADMIN) as http:
        assert http.delete("/catalogs/main/schemas/m3/tables/fragile").status_code == 204
        refused = http.post("/catalogs/main/schemas/m3/tables/fragile_sum:refresh")
        assert refused.status_code == 403
        assert refused.json()["type"].endswith("materialized-view-definer-access")
    assert admin.sql("SELECT total FROM main.m3.fragile_sum")["total"][0] > 0


# ------------------------------------------------------------------ result cache
def worker_cache(stack):
    import httpx

    worker = stack.workers["sql"]
    reply = httpx.get(
        worker.url + "/internal/v1/health",
        headers={
            "Authorization": "Bearer " + stack.worker_settings.worker_token.get_secret_value()
        },
    )
    return reply.json()["result_cache"]


def cached(admin, sql):
    return admin.request("POST", "/statements", json={"sql": sql})["result_cache"]


def test_identical_statements_over_unchanged_files_are_answered_from_memory(stack, admin, tmp_path):
    make_table(admin, tmp_path, "hot", files=1)
    sql = "SELECT region, count(*) AS n FROM main.m3.hot WHERE k >= 0 GROUP BY 1 ORDER BY 1"
    assert cached(admin, sql) == "miss"
    assert cached(admin, sql) == "hit"
    first = admin.sql(sql)
    assert admin.sql(sql).equals(first)
    assert worker_cache(stack)["hits"] >= 2

    # A commit changes the file list, so the old answer cannot be served: no stale reads, ever.
    admin.upload("main.m3.hot", [scattered(tmp_path, "hot-more", seed=5, rows=300)], mode="append")
    assert cached(admin, sql) == "miss"
    assert admin.sql(sql)["n"].sum() == 800
    assert cached(admin, sql) == "hit"


def test_the_cache_never_answers_for_someone_who_may_not_ask(stack, admin, tmp_path):
    make_table(admin, tmp_path, "private", files=1)
    sql = "SELECT count(*) AS n FROM main.m3.private WHERE amount >= 0"
    assert cached(admin, sql) == "miss" and cached(admin, sql) == "hit"
    outsider = stack.client("mallory", "nobody")
    with pytest.raises(DataError) as denied:
        outsider.sql(sql)  # same SQL, same files, warm cache: still refused by the planner
    assert denied.value.status == 404


def test_statements_that_can_change_between_runs_are_never_cached(stack, admin, tmp_path):
    make_table(admin, tmp_path, "clock", files=1)
    for sql in (
        "SELECT random() AS r FROM main.m3.clock LIMIT 1",
        "SELECT now() AS t FROM main.m3.clock LIMIT 1",
        "SELECT current_date AS d FROM main.m3.clock LIMIT 1",
    ):
        assert cached(admin, sql) == "miss" and cached(admin, sql) == "miss"


# ------------------------------------------------------------------ snapshot expiry and orphans
def object_exists(stack, uri: str) -> bool:
    import boto3
    from botocore.exceptions import ClientError

    bucket, _, key = uri.removeprefix("s3://").partition("/")
    client = boto3.client(
        "s3",
        endpoint_url=stack.settings.s3_endpoint_url,
        aws_access_key_id=stack.settings.s3_access_key_id.get_secret_value(),
        aws_secret_access_key=stack.settings.s3_secret_access_key.get_secret_value(),
        region_name="us-east-1",
    )
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def planned(stack, name):
    services = stack.app.state.services
    record = services.store.table("main", "m3", name)
    return [f.path for f in services.tables.plan(record, []).files]


def run_expire(stack, name, body, monkeypatch=None, days_ahead=0):
    from datetime import UTC, datetime, timedelta

    from substrate_data.api.routes import maintenance

    if days_ahead:
        monkeypatch.setattr(
            maintenance, "utcnow", lambda: datetime.now(UTC) + timedelta(days=days_ahead)
        )
    with stack.http("alice", *ADMIN) as http:
        reply = http.post(f"/catalogs/main/schemas/m3/tables/{name}:expire", json=body)
        assert reply.status_code == 202, reply.text
        run = reply.json()
        if run["status"] == "running":
            run = wait_for(
                lambda: http.get(
                    f"/catalogs/main/schemas/m3/tables/{name}/maintenance-runs/{run['id']}"
                ).json(),
                lambda r: r["status"] in {"succeeded", "failed"},
            )
        return run


def test_expiring_snapshots_deletes_only_files_no_snapshot_still_uses(
    stack, admin, tmp_path, monkeypatch
):
    make_table(admin, tmp_path, "aging", files=4)
    before = planned(stack, "aging")
    assert run_optimize(stack, "aging", {"strategy": "compact"})["status"] == "succeeded"
    merged = planned(stack, "aging")
    assert len(before) == 4 and len(merged) == 1
    assert all(object_exists(stack, f) for f in before)  # time travel still needs them
    answer = admin.sql("SELECT count(*) AS n, sum(k) AS s FROM main.m3.aging")

    # Fresh snapshots are not touched: expiry only removes what is older than the window.
    nothing = run_expire(stack, "aging", {"older_than_hours": 24, "retain_last": 1})
    assert nothing["files_in"] == 0 and all(object_exists(stack, f) for f in before)

    done = run_expire(
        stack, "aging", {"older_than_hours": 24, "retain_last": 1}, monkeypatch, days_ahead=3
    )
    assert done["status"] == "succeeded" and done["params"]["snapshots_expired"] >= 4
    assert done["files_in"] == 4 and done["files_out"] == 4
    assert not any(object_exists(stack, f) for f in before)  # the superseded files are gone
    assert all(object_exists(stack, f) for f in merged)  # the live file is untouched
    assert admin.sql("SELECT count(*) AS n, sum(k) AS s FROM main.m3.aging").equals(answer)
    assert table(admin, "aging")["row_count"] == 2000


def test_the_newest_snapshots_are_always_retained(stack, admin, tmp_path, monkeypatch):
    make_table(admin, tmp_path, "keepers", files=3)
    run = run_expire(
        stack, "keepers", {"older_than_hours": 1, "retain_last": 3}, monkeypatch, days_ahead=30
    )
    assert run["params"]["snapshots_expired"] == 0 and run["files_in"] == 0
    assert admin.sql("SELECT count(*) AS n FROM main.m3.keepers")["n"][0] == 1500


def test_orphans_are_removed_only_when_old_and_unreferenced(stack, admin, tmp_path, monkeypatch):
    import io

    import boto3

    make_table(admin, tmp_path, "strays", files=2)
    services = stack.app.state.services
    record = services.store.table("main", "m3", "strays")
    location = services.tables.load(record).location().rstrip("/")
    bucket, _, prefix = location.removeprefix("s3://").partition("/")
    s3 = boto3.client(
        "s3",
        endpoint_url=stack.settings.s3_endpoint_url,
        aws_access_key_id=stack.settings.s3_access_key_id.get_secret_value(),
        aws_secret_access_key=stack.settings.s3_secret_access_key.get_secret_value(),
        region_name="us-east-1",
    )
    buffer = io.BytesIO()
    pl.DataFrame({"k": [1]}).write_parquet(buffer)
    stray = f"{prefix}/data/{uuid4().hex}/abandoned.parquet"
    s3.put_object(Bucket=bucket, Key=stray, Body=buffer.getvalue())
    uri = f"s3://{bucket}/{stray}"
    live = planned(stack, "strays")

    young = run_expire(stack, "strays", {"remove_orphans": True, "orphan_min_age_hours": 24})
    assert young["files_in"] == 0 and object_exists(stack, uri)  # might belong to a write in flight

    old = run_expire(
        stack,
        "strays",
        {"remove_orphans": True, "orphan_min_age_hours": 24},
        monkeypatch,
        days_ahead=2,
    )
    assert old["status"] == "succeeded" and old["files_in"] == 1
    assert not object_exists(stack, uri)
    assert all(object_exists(stack, f) for f in live)  # referenced files are never orphans
    assert admin.sql("SELECT count(*) AS n FROM main.m3.strays")["n"][0] == 1000


def test_the_delete_job_refuses_anything_but_table_data_files(stack):
    from substrate_data.compute.manifests import DeletePayload

    s = stack.worker_settings
    ok = f"s3://{s.warehouse}/ns/t/data/w/f.parquet"
    DeletePayload(paths=[ok]).check(s)
    for bad in (
        f"s3://{s.warehouse}/ns/t/metadata/v1.metadata.json",
        f"s3://{s.warehouse}/ns/t/data/../../other/f.parquet",
        f"s3://{s.staging_bucket}/uploads/f.parquet",
        "s3://elsewhere/ns/t/data/f.parquet",
        f"s3://{s.warehouse}/ns/t/data/f.csv",
    ):
        with pytest.raises(ValueError):
            DeletePayload(paths=[bad]).check(s)


# ------------------------------------------------------------------ z-order and bloom filters
def scatter_2d(tmp_path, name, rows=60_000):
    import random

    rng = random.Random(7)
    path = tmp_path / f"{name}.parquet"
    pl.DataFrame(
        {
            "x": [rng.randrange(1000) for _ in range(rows)],
            "y": [rng.randrange(1000) for _ in range(rows)],
            "id": list(range(rows)),
            "pad": [rng.randrange(10**9) for _ in range(rows)],
        }
    ).write_parquet(path)
    return path


def column_ranges(stack, files, column):
    import pyarrow.parquet as pq

    from substrate_data.compute.batch import filesystem

    fs = filesystem(stack.worker_settings)
    ranges = []
    for f in files:
        meta = pq.read_metadata(f["path"].removeprefix("s3://"), filesystem=fs)
        index = meta.schema.names.index(column)
        lows = [meta.row_group(g).column(index).statistics.min for g in range(meta.num_row_groups)]
        highs = [meta.row_group(g).column(index).statistics.max for g in range(meta.num_row_groups)]
        ranges.append((min(lows), max(highs)))
    return ranges


def optimize_payload(stack, tmp_path, name, **extra):
    admin = stack.client("alice", *ADMIN)
    admin.upload(f"main.m3.{name}", [scatter_2d(tmp_path, name)])
    services = stack.app.state.services
    record = services.store.table("main", "m3", name)
    return {
        "source_files": [f.path for f in services.tables.plan(record, []).files],
        "sort_by": [],
        "zorder": False,
        "bloom_columns": [],
        "location": services.tables.load(record).location(),
        "write_id": uuid4().hex,
        "partitioning": [],
        "target_file_bytes": 250_000,
        "row_group_rows": 5000,
        **extra,
    }


def test_zorder_narrows_file_ranges_in_every_column_where_a_sort_does_it_for_one(
    stack, admin, tmp_path
):
    plain = batch.optimize(
        optimize_payload(stack, tmp_path, "z_plain", sort_by=["x", "y"]), stack.worker_settings
    )
    zordered = batch.optimize(
        optimize_payload(stack, tmp_path, "z_order", sort_by=["x", "y"], zorder=True),
        stack.worker_settings,
    )
    assert len(plain["files"]) >= 4 and len(zordered["files"]) >= 4
    assert plain["rows"] == zordered["rows"] == 60_000

    def overlapping(result, column, low, high):
        return sum(
            1
            for lo, hi in column_ranges(stack, result["files"], column)
            if lo <= high and hi >= low
        )

    # A box near the origin in y: after a lexicographic (x, y) sort every file spans all of y;
    # after Z-ordering only the files whose cell touches that corner do.
    sort_hits = overlapping(plain, "y", 0, 80)
    z_hits = overlapping(zordered, "y", 0, 80)
    assert sort_hits == len(plain["files"])
    assert z_hits < sort_hits * 0.75, (z_hits, sort_hits)
    # And it still helps the first column just as well.
    assert overlapping(zordered, "x", 0, 80) < len(zordered["files"]) * 0.75


def test_bloom_filters_are_written_and_let_duckdb_rule_out_row_groups(stack, admin, tmp_path):
    import duckdb

    from substrate_data.compute.batch import filesystem

    result = batch.optimize(
        optimize_payload(stack, tmp_path, "blooms", bloom_columns=["id"], target_file_bytes=10**9),
        stack.worker_settings,
    )
    assert result["rows"] == 60_000 and len(result["files"]) == 1
    fs = filesystem(stack.worker_settings)
    local = tmp_path / "bloom.parquet"
    with fs.open_input_file(result["files"][0]["path"].removeprefix("s3://")) as src:
        local.write_bytes(src.read())
    db = duckdb.connect()
    present = db.execute(
        f"SELECT bloom_filter_excludes FROM parquet_bloom_probe('{local}', 'id', 31337)"
    ).fetchall()
    absent = db.execute(
        f"SELECT bloom_filter_excludes FROM parquet_bloom_probe('{local}', 'id', 99999999)"
    ).fetchall()
    assert len(present) > 4 and not all(
        row[0] for row in present
    )  # the group holding it is kept...
    assert sum(1 for row in present if row[0]) >= len(present) - 2  # ...and the others are skipped
    assert absent and all(row[0] for row in absent)  # one that is not can be skipped
    assert db.execute(f"SELECT x FROM '{local}' WHERE id = 31337").fetchall()  # and is still found
    # A column without a filter is not probeable.
    plain = batch.optimize(optimize_payload(stack, tmp_path, "nobloom"), stack.worker_settings)
    other = tmp_path / "plain.parquet"
    with fs.open_input_file(plain["files"][0]["path"].removeprefix("s3://")) as src:
        other.write_bytes(src.read())
    row = db.execute(
        f"SELECT bloom_filter_excludes FROM parquet_bloom_probe('{other}', 'id', 99999999)"
    ).fetchall()
    assert not any(r[0] for r in row)


def test_bloom_rewrites_keep_partitions_and_the_table_answers_the_same(stack, admin, tmp_path):
    months = [datetime(2026, m, d) for m in (1, 2, 3) for d in (1, 9, 17, 25)]
    path = tmp_path / "ev.parquet"
    pl.DataFrame({"ts": months * 50, "id": list(range(len(months) * 50))}).write_parquet(path)
    admin.upload("main.m3.bloom_ev", [path], partitioning=[{"column": "ts", "transform": "month"}])
    admin.upload("main.m3.bloom_ev", [path], mode="append")
    before = admin.sql(
        "SELECT month(ts) AS m, count(*) AS n, sum(id) AS s FROM main.m3.bloom_ev GROUP BY 1 ORDER BY 1"
    )
    run = run_optimize(stack, "bloom_ev", {"strategy": "compact", "bloom_columns": ["id"]})
    assert run["status"] == "succeeded", run["error"]
    assert table(admin, "bloom_ev")["file_count"] == 3  # one file per month again
    assert admin.sql(
        "SELECT month(ts) AS m, count(*) AS n, sum(id) AS s FROM main.m3.bloom_ev GROUP BY 1 ORDER BY 1"
    ).equals(before)
    pruned = admin.request(
        "POST",
        "/statements",
        json={"sql": "SELECT count(*) FROM main.m3.bloom_ev WHERE ts >= '2026-03-01'"},
    )
    assert pruned["files_scanned"] == 1


def test_the_api_accepts_zorder_and_validates_its_shape(stack, admin, tmp_path):
    make_table(admin, tmp_path, "z_api", files=3)
    with stack.http("alice", *ADMIN) as http:
        url = "/catalogs/main/schemas/m3/tables/z_api:optimize"
        for bad in (
            {"strategy": "zorder", "sort_by": ["k"]},
            {"strategy": "zorder", "sort_by": ["k", "region", "amount", "k", "k"]},
            {"strategy": "compact", "sort_by": ["k"]},
            {"strategy": "sort", "sort_by": ["k"], "bloom_columns": ["nope"]},
        ):
            assert http.post(url, json=bad).status_code == 400, bad
    run = run_optimize(
        stack, "z_api", {"strategy": "zorder", "sort_by": ["k", "amount"], "bloom_columns": ["k"]}
    )
    assert run["status"] == "succeeded", run
    assert admin.sql("SELECT count(*) AS n FROM main.m3.z_api")["n"][0] == 1500


# ------------------------------------------------------------------ partitioned, incremental, scheduled
def mv_post(stack, body, expect=202):
    with stack.http("alice", *ADMIN) as http:
        reply = http.post("/catalogs/main/schemas/m3/materialized-views", json=body)
        assert reply.status_code == expect, reply.text
        return reply.json()


def refresh(stack, name, full=False):
    with stack.http("alice", *ADMIN) as http:
        reply = http.post(
            f"/catalogs/main/schemas/m3/tables/{name}:refresh", params={"full": str(full).lower()}
        )
        assert reply.status_code == 202, reply.text
        run = reply.json()
        if run["status"] == "running":
            run = wait_for(
                lambda: http.get(
                    f"/catalogs/main/schemas/m3/tables/{name}/maintenance-runs/{run['id']}"
                ).json(),
                lambda r: r["status"] in {"succeeded", "failed", "skipped"},
            )
        return run


def events_file(tmp_path, name, months, per=40, start=0):
    path = tmp_path / f"{name}.parquet"
    ts = [datetime(2026, m, 1 + (i % 27)) for m in months for i in range(per)]
    pl.DataFrame(
        {"ts": ts, "id": list(range(start, start + len(ts))), "amount": [1.0] * len(ts)}
    ).write_parquet(path)
    return path


def test_a_materialized_view_can_be_partitioned_like_any_table(stack, admin, tmp_path):
    admin.upload("main.m3.pv_src", [events_file(tmp_path, "pv", [1, 2, 3])])
    mv_post(
        stack,
        {
            "name": "pv_copy",
            "sql": "SELECT ts, id, amount FROM main.m3.pv_src",
            "partitioning": [{"column": "ts", "transform": "month"}],
        },
    )
    view = wait_fresh(stack, "pv_copy")
    assert view["materialized"]["status"] == "fresh", view["materialized"]["error"]
    assert view["file_count"] == 3 and view["row_count"] == 120  # one file per month
    pruned = admin.request(
        "POST",
        "/statements",
        json={"sql": "SELECT count(*) FROM main.m3.pv_copy WHERE ts >= '2026-03-01'"},
    )
    assert pruned["files_scanned"] == 1


def test_incremental_refresh_reads_only_new_files_and_matches_a_full_recompute(
    stack, admin, tmp_path
):
    admin.upload("main.m3.inc_src", [events_file(tmp_path, "i0", [1, 2], start=0)])
    mv_post(
        stack,
        {
            "name": "inc_view",
            "sql": "SELECT ts, id, amount * 2 AS doubled FROM main.m3.inc_src WHERE id % 2 = 0",
            "incremental": True,
        },
    )
    first = wait_fresh(stack, "inc_view")
    assert first["materialized"]["incremental"] is True and first["row_count"] == 40

    nothing = refresh(stack, "inc_view")
    assert nothing["status"] == "skipped"  # no new data: no job at all

    admin.upload("main.m3.inc_src", [events_file(tmp_path, "i1", [3], start=1000)], mode="append")
    assert table(admin, "inc_view")["materialized"]["stale"] is True
    run = refresh(stack, "inc_view")
    assert run["status"] == "succeeded" and run["params"]["mode"] == "incremental"
    assert run["files_in"] == 1  # only the one new source file was read
    after = table(admin, "inc_view")
    assert after["row_count"] == 60 and after["materialized"]["stale"] is False

    full = admin.sql(
        "SELECT ts, id, amount * 2 AS doubled FROM main.m3.inc_src WHERE id % 2 = 0 ORDER BY id"
    )
    stored = admin.sql("SELECT * FROM main.m3.inc_view ORDER BY id")
    assert stored.equals(full)  # exactly what recomputing everything would give

    # A forced full refresh rebuilds the same table.
    rebuilt = refresh(stack, "inc_view", full=True)
    assert rebuilt["params"]["mode"] == "full" and table(admin, "inc_view")["row_count"] == 60


def test_incremental_falls_back_to_a_full_refresh_when_the_source_was_rewritten(
    stack, admin, tmp_path
):
    admin.upload("main.m3.rw_src", [events_file(tmp_path, "r0", [1])], mode="create")
    admin.upload("main.m3.rw_src", [events_file(tmp_path, "r1", [2], start=500)], mode="append")
    mv_post(
        stack, {"name": "rw_view", "sql": "SELECT ts, id FROM main.m3.rw_src", "incremental": True}
    )
    wait_fresh(stack, "rw_view")
    assert run_optimize(stack, "rw_src", {"strategy": "compact"})["status"] == "succeeded"
    admin.upload("main.m3.rw_src", [events_file(tmp_path, "r2", [3], start=900)], mode="append")
    run = refresh(stack, "rw_view")
    assert run["status"] == "succeeded" and run["params"]["mode"] == "full"  # not trusted to append
    assert table(admin, "rw_view")["row_count"] == 120
    assert (
        admin.sql("SELECT count(DISTINCT id) AS n FROM main.m3.rw_view")["n"][0] == 120
    )  # no duplicates


def test_definitions_that_cannot_be_refreshed_incrementally_are_refused_at_creation(
    stack, admin, tmp_path
):
    admin.upload("main.m3.inc_bad", [events_file(tmp_path, "b0", [1])])
    for sql in (
        "SELECT count(*) AS n FROM main.m3.inc_bad",
        "SELECT DISTINCT id FROM main.m3.inc_bad",
        "SELECT a.id FROM main.m3.inc_bad a JOIN main.m3.inc_bad b ON a.id = b.id",
        "SELECT id, random() AS r FROM main.m3.inc_bad",
    ):
        reply = mv_post(stack, {"name": "never", "sql": sql, "incremental": True}, expect=400)
        assert "Incremental refresh" in str(reply)
    with pytest.raises(DataError):
        admin.table("main.m3.never")  # nothing was created


def test_scheduled_views_refresh_themselves_only_when_a_source_changed(stack, admin, tmp_path):
    admin.upload("main.m3.sch_src", [events_file(tmp_path, "s0", [1])])
    mv_post(
        stack,
        {
            "name": "sch_view",
            "sql": "SELECT count(*) AS n FROM main.m3.sch_src",
            "refresh_every_minutes": 5,
        },
    )
    wait_fresh(stack, "sch_view")
    services, publisher = stack.app.state.services, stack.app.state.publisher

    def tick() -> int:
        """Ask the running publisher for a scheduling pass now, and wait for it to finish."""
        done = publisher.schedule_runs
        publisher.last_schedule = 0.0
        publisher.wake.set()
        wait_for(lambda: publisher.schedule_runs, lambda n: n > done, timeout=20)
        return publisher.scheduled

    def make_due():
        record = services.store.table("main", "m3", "sch_view")
        info = {**record["materialized"]}
        info["refreshed_at"] = (datetime.now(UTC) - timedelta(minutes=6)).isoformat()
        services.store.update_table_state(record["id"], {"materialized": info})

    assert tick() == 0  # fresh and not due
    admin.upload("main.m3.sch_src", [events_file(tmp_path, "s1", [2])], mode="append")
    assert tick() == 0  # stale, but not due for five minutes
    make_due()
    assert tick() == 1  # stale and due: it refreshes itself, as its owner
    wait_for(
        lambda: table(admin, "sch_view"),
        lambda t: t["materialized"]["status"] == "fresh" and t["materialized"]["stale"] is False,
    )
    assert admin.sql("SELECT n FROM main.m3.sch_view")["n"][0] == 80
    make_due()
    assert tick() == 0  # due, but nothing changed since: no work is done
