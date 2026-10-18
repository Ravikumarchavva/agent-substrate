"""Row filters and column masks, enforced end to end. Every test tries to get around them."""

from __future__ import annotations

import time

import polars as pl
import pytest
from conftest import ADMIN

from loamz.data.client import DataError

pytestmark = pytest.mark.integration

EU, US, AUDIT = ("eu-analysts",), ("us-analysts",), ("auditors",)
PEOPLE = pl.DataFrame(
    {
        "id": list(range(1, 9)),
        "name": ["Ana", "Ben", "Cleo", "Dev", "Eli", "Fay", "Gus", "Hal"],
        "email": [
            f"{n}@corp.example" for n in ["ana", "ben", "cleo", "dev", "eli", "fay", "gus", "hal"]
        ],
        "region": ["EU", "EU", "EU", "EU", "US", "US", "US", "US"],
        "salary": [
            90_000.0,
            120_000.0,
            70_000.0,
            100_000.0,
            85_000.0,
            95_000.0,
            130_000.0,
            60_000.0,
        ],
    }
)
ROW_FILTER = (
    "(is_member('eu-analysts') AND region = 'EU') OR (is_member('us-analysts') AND region = 'US')"
)
MASKS = [
    {"column": "email", "mask": "hash"},
    {"column": "salary", "mask": "null"},
    {"column": "name", "mask": "redact"},
]
EXEMPT = ["group:auditors", "group:data-admins"]


def put(stack, table, body, expect=200):
    with stack.http("alice", *ADMIN) as http:
        reply = http.put(f"/catalogs/main/schemas/pol/tables/{table}/policy", json=body)
        assert reply.status_code == expect, reply.text
        return reply.json()


def grant(stack, kind, name, group, privilege="SELECT"):
    with stack.http("alice", *ADMIN) as http:
        reply = http.patch(
            f"/grants/{kind}/{name}",
            json={"changes": [{"principal": f"group:{group}", "add": [privilege]}]},
        )
        assert reply.status_code == 200, reply.text


@pytest.fixture(scope="module")
def people(stack, tmp_path_factory):
    admin = stack.client("alice", *ADMIN)
    admin.create_schema("main", "pol")
    path = tmp_path_factory.mktemp("pol") / "people.parquet"
    PEOPLE.write_parquet(path)
    admin.upload(
        "main.pol.people", [path], partitioning=[{"column": "region", "transform": "identity"}]
    )
    for group in ("eu-analysts", "us-analysts", "auditors", "readers"):
        grant(stack, "schema", "main.pol", group, "USE_SCHEMA")
        grant(stack, "table", "main.pol.people", group)
    put(stack, "people", {"row_filter": ROW_FILTER, "column_masks": MASKS, "exempt": EXEMPT})
    return admin


def client(stack, name, groups):
    return stack.client(name, *groups)


def test_each_reader_sees_only_the_rows_their_group_may(stack, people):
    eu = client(stack, "erin", EU).sql("SELECT id, region FROM main.pol.people ORDER BY id")
    us = client(stack, "ulf", US).sql("SELECT id, region FROM main.pol.people ORDER BY id")
    assert eu["region"].to_list() == ["EU"] * 4 and us["region"].to_list() == ["US"] * 4
    nobody = client(stack, "nina", ("readers",)).sql("SELECT count(*) AS n FROM main.pol.people")
    assert nobody["n"][0] == 0  # readers match no rule: they see an empty table, not an error


def test_masked_columns_are_masked_and_exempt_readers_see_the_truth(stack, people):
    masked = client(stack, "erin", EU).sql("SELECT * FROM main.pol.people ORDER BY id")
    assert masked["salary"].null_count() == 4 and masked["salary"].dtype == pl.Float64
    assert set(masked["name"]) == {"***"}
    assert all(len(h) == 64 and "@" not in h for h in masked["email"])  # salted SHA-256, hex
    assert masked["email"].n_unique() == 4  # stable per value, so joins and counts still work
    auditor = client(stack, "ava", AUDIT).sql("SELECT * FROM main.pol.people ORDER BY id")
    assert auditor["email"].to_list() == PEOPLE["email"].to_list()
    assert auditor["salary"].to_list() == PEOPLE["salary"].to_list()
    assert len(auditor) == 8 and auditor["name"].to_list() == PEOPLE["name"].to_list()


def test_the_salt_makes_hashes_unguessable_from_the_value(stack, people):
    import hashlib

    masked = client(stack, "erin", EU).sql("SELECT email FROM main.pol.people WHERE id = 1")[
        "email"
    ][0]
    assert masked != hashlib.sha256(b"ana@corp.example").hexdigest()  # not a plain SHA-256
    assert masked == hashlib.sha256(b"test-salt-9f3a" + b"ana@corp.example").hexdigest()


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) AS n FROM main.pol.people WHERE email = 'ana@corp.example'",
        "SELECT count(*) AS n FROM main.pol.people WHERE email LIKE 'ana%'",
        "SELECT count(*) AS n FROM main.pol.people WHERE salary > 0",
        "SELECT count(*) AS n FROM main.pol.people WHERE name = 'Ana'",
        "SELECT count(*) AS n FROM (SELECT * FROM main.pol.people) q WHERE q.salary = 90000",
        "WITH p AS (SELECT * FROM main.pol.people) SELECT count(*) AS n FROM p WHERE name <> '***'",
        "SELECT count(*) AS n FROM main.pol.people a JOIN main.pol.people b ON a.salary = b.salary",
        "SELECT count(*) AS n FROM main.pol.people WHERE id IN (SELECT id FROM main.pol.people WHERE salary > 0)",
        "SELECT count(*) AS n FROM main.pol.people p WHERE EXISTS (SELECT 1 FROM main.pol.people q WHERE q.name = 'Ana')",
    ],
)
def test_predicates_on_hidden_values_find_nothing(stack, people, sql):
    assert client(stack, "erin", EU).sql(sql)["n"][0] == 0  # nothing matches the real values


def test_grouping_ordering_and_counting_only_see_what_the_reader_may(stack, people):
    erin = client(stack, "erin", EU)
    assert erin.sql("SELECT count(*) AS n FROM main.pol.people")["n"][0] == 4  # not 8
    assert erin.sql("SELECT sum(salary) AS s FROM main.pol.people")["s"][0] is None
    assert erin.sql("SELECT region, count(*) AS n FROM main.pol.people GROUP BY 1").rows() == [
        ("EU", 4)
    ]
    assert erin.sql("SELECT DISTINCT region FROM main.pol.people")["region"].to_list() == ["EU"]
    assert erin.sql("SELECT max(id) AS m FROM main.pol.people")["m"][0] == 4


def test_filters_on_other_regions_just_return_nothing_and_skip_the_files(stack, people):
    erin = client(stack, "erin", EU)
    result = erin.request(
        "POST", "/statements", json={"sql": "SELECT * FROM main.pol.people WHERE region = 'US'"}
    )
    assert result["row_count"] == 0
    auditor = client(stack, "ava", AUDIT)
    both = auditor.request("POST", "/statements", json={"sql": "SELECT * FROM main.pol.people"})
    one = auditor.request(
        "POST", "/statements", json={"sql": "SELECT * FROM main.pol.people WHERE region = 'US'"}
    )
    assert one["files_scanned"] < both["files_scanned"]  # exempt readers still get pruning
    assert (
        erin.request("POST", "/statements", json={"sql": "SELECT * FROM main.pol.people"})[
            "files_scanned"
        ]
        == 1
    )  # the row filter alone narrowed Erin's scan to the EU partition


def test_a_view_is_judged_by_its_creators_standing_not_the_readers(stack, people):
    grant(stack, "schema", "main.pol", "eu-analysts", "CREATE_TABLE")
    carol = client(stack, "carol", EU)
    carol.create_view("main.pol.eu_roster", "SELECT id, email, salary, region FROM main.pol.people")
    grant(stack, "view", "main.pol.eu_roster", "us-analysts")
    ulf = client(stack, "ulf", US).sql("SELECT * FROM main.pol.eu_roster ORDER BY id")
    assert ulf["region"].to_list() == ["EU"] * 4  # Ulf is US, but the view reads as Carol (EU)
    assert ulf["salary"].null_count() == 4  # and as someone for whom salary is masked
    # Ulf still cannot read the table itself beyond his own rule.
    assert client(stack, "ulf", US).sql("SELECT count(*) AS n FROM main.pol.people")["n"][0] == 4


def test_a_view_by_an_exempt_owner_is_a_deliberate_share(stack, people):
    admin = stack.client("alice", *ADMIN)
    admin.create_view(
        "main.pol.region_pay",
        "SELECT region, avg(salary) AS avg_pay FROM main.pol.people GROUP BY 1",
    )
    grant(stack, "view", "main.pol.region_pay", "readers")
    rows = client(stack, "nina", ("readers",)).sql(
        "SELECT * FROM main.pol.region_pay ORDER BY region"
    )
    assert (
        rows["avg_pay"].null_count() == 0
    )  # aggregates computed on the real salaries, shared on purpose


def test_policy_changes_apply_to_the_very_next_statement_even_with_a_warm_cache(stack, people):
    erin = client(stack, "erin", EU)
    sql = "SELECT count(*) AS n FROM main.pol.people"
    assert erin.request("POST", "/statements", json={"sql": sql})["row_count"] == 1
    assert erin.request("POST", "/statements", json={"sql": sql})["result_cache"] == "hit"
    put(
        stack,
        "people",
        {"row_filter": "region = 'EU' AND id <= 2", "column_masks": MASKS, "exempt": EXEMPT},
    )
    try:
        assert erin.sql(sql)["n"][0] == 2  # the old answer (4) is not served from memory
    finally:
        put(stack, "people", {"row_filter": ROW_FILTER, "column_masks": MASKS, "exempt": EXEMPT})
    assert erin.sql(sql)["n"][0] == 4


def test_agents_get_tenant_isolation_from_the_same_mechanism(stack, tmp_path):
    admin = stack.client("alice", *ADMIN)
    path = tmp_path / "orders.parquet"
    pl.DataFrame(
        {"tenant": ["acme", "acme", "beta", "beta", "beta"], "amount": [1.0, 2.0, 10.0, 20.0, 30.0]}
    ).write_parquet(path)
    admin.upload("main.pol.orders", [path])
    grant(stack, "schema", "main.pol", "tenant-acme", "USE_SCHEMA")
    grant(stack, "schema", "main.pol", "tenant-beta", "USE_SCHEMA")
    for tenant in ("acme", "beta"):
        grant(stack, "table", "main.pol.orders", f"tenant-{tenant}")
    put(stack, "orders", {"row_filter": "tenant = current_tenant()", "exempt": EXEMPT})
    acme = stack.agent_client("user-1", "acme").sql("SELECT sum(amount) AS s FROM main.pol.orders")
    beta = stack.agent_client("user-2", "beta").sql("SELECT sum(amount) AS s FROM main.pol.orders")
    assert acme["s"][0] == 3.0 and beta["s"][0] == 60.0
    assert admin.sql("SELECT sum(amount) AS s FROM main.pol.orders")["s"][0] == 63.0  # exempt admin


def test_a_materialized_view_holds_what_its_definer_may_see(stack, people):
    with stack.http("erin", *EU) as http:
        grant(stack, "schema", "main.pol", "eu-analysts", "CREATE_TABLE")
        created = http.post(
            "/catalogs/main/schemas/pol/materialized-views",
            json={
                "name": "eu_copy",
                "sql": "SELECT id, email, salary, region FROM main.pol.people",
            },
        )
        assert created.status_code == 202, created.text
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            view = http.get("/catalogs/main/schemas/pol/tables/eu_copy").json()
            if view["materialized"]["status"] in {"fresh", "failed"}:
                break
            time.sleep(0.5)
    assert view["materialized"]["status"] == "fresh" and view["row_count"] == 4
    grant(stack, "table", "main.pol.eu_copy", "readers")
    copy = client(stack, "nina", ("readers",)).sql("SELECT * FROM main.pol.eu_copy")
    assert copy["salary"].null_count() == 4 and set(copy["region"]) == {
        "EU"
    }  # masked at the source


def test_only_the_owner_or_an_admin_manages_policies_and_bad_ones_are_refused(stack, people):
    with stack.http("erin", *EU) as http:
        for call in (
            lambda: http.get("/catalogs/main/schemas/pol/tables/people/policy"),
            lambda: http.put(
                "/catalogs/main/schemas/pol/tables/people/policy", json={"row_filter": "true"}
            ),
            lambda: http.delete("/catalogs/main/schemas/pol/tables/people/policy"),
        ):
            assert call().status_code == 403
    for bad in (
        {"row_filter": "id IN (SELECT id FROM main.pol.orders)"},
        {"row_filter": "read_csv('/etc/passwd') = 1"},
        {"row_filter": "ghost = 1"},
        {"column_masks": [{"column": "nope", "mask": "null"}]},
        {"column_masks": [{"column": "id", "mask": "expression"}]},
        {"exempt": ["alice"]},
    ):
        put(stack, "people", bad, expect=400)
    assert (
        put(stack, "people", {"row_filter": ROW_FILTER, "column_masks": MASKS, "exempt": EXEMPT})[
            "version"
        ]
        >= 1
    )


def test_removing_a_policy_restores_full_access_and_is_audited(stack, people):
    with stack.http("alice", *ADMIN) as http:
        assert http.delete("/catalogs/main/schemas/pol/tables/people/policy").status_code == 204
        assert http.get("/catalogs/main/schemas/pol/tables/people/policy").status_code == 404
    try:
        full = client(stack, "erin", EU).sql("SELECT * FROM main.pol.people")
        assert len(full) == 8 and full["salary"].null_count() == 0
        events = stack.app.state.container.store.audit_events(None, 200)
        assert {"table.policy.set", "table.policy.delete"} <= {e["action"] for e in events}
    finally:
        put(stack, "people", {"row_filter": ROW_FILTER, "column_masks": MASKS, "exempt": EXEMPT})


def test_the_salt_never_appears_in_history_or_to_clients(stack, people):
    erin = client(stack, "erin", EU)
    erin.sql("SELECT email FROM main.pol.people")
    history = erin.request("GET", "/statements")["statements"]
    assert history and "test-salt-9f3a" not in str(history)
    with pytest.raises(DataError):
        erin.request("GET", "/catalogs/main/schemas/pol/tables/people/policy")  # 403: not the owner
