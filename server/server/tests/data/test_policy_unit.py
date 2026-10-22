"""The policy expression language: what it accepts, what it refuses, and what it rewrites to."""

from __future__ import annotations

import pytest
from sqlglot import exp

from loamz.engine.errors import LoamzError, http_status
from loamz.server.data.features.sql.policy import (
    applies,
    parse,
    resolve_context,
    validate_expression,
    validate_policy,
    wrap,
)
from loamz.server.data.kernel.principal import Principal

COLUMNS = [
    {"name": "id", "type": "BIGINT"},
    {"name": "email", "type": "VARCHAR"},
    {"name": "region", "type": "VARCHAR"},
    {"name": "salary", "type": "DOUBLE"},
]


def who(name="alice", *groups, **context) -> Principal:
    return Principal(
        subject=name, name=name, kind="user", groups=frozenset(groups), context=context
    )


@pytest.mark.parametrize(
    "text",
    [
        "(select 1)",
        "region IN (SELECT region FROM other)",
        "exists(select 1)",
        "read_csv('/etc/passwd') = 1",
        "getenv('SECRET') = 'x'",
        "other.region = 'a'",
        "nope = 1",
        "region = (SELECT max(region) FROM t)",
        "list_transform([1], x -> x) = [1]",
        "region = ?",
        "salary > 1 AND query('select 1')",
        "",
        "x" * 3000,
    ],
)
def test_expressions_that_could_reach_beyond_the_row_are_refused(text):
    with pytest.raises(LoamzError) as refused:
        validate_expression(parse(text), {"region", "salary"})
    assert http_status(refused.value) == 400


@pytest.mark.parametrize(
    "text",
    [
        "region = 'EU'",
        "is_member('analysts') AND salary < 100000 OR current_user() = 'root'",
        "CASE WHEN region = 'EU' THEN true ELSE false END",
        "upper(region) IN ('EU', 'UK') AND salary BETWEEN 0 AND 10",
        "coalesce(region, 'none') <> 'x' AND current_tenant() IS NOT NULL",
    ],
)
def test_ordinary_rules_are_accepted(text):
    validate_expression(parse(text), {"region", "salary"})


def test_context_functions_become_literals_and_the_rule_collapses_for_the_reader():
    tree = parse("is_member('eu') AND region = current_user() AND current_tenant() = 'acme'")
    mine = resolve_context(tree, who("eu-bob", "eu", tenant_id="acme")).sql("duckdb")
    assert mine == "region = 'eu-bob'"  # the facts about Bob are folded in, leaving a plain filter
    other = resolve_context(tree, who("sam")).sql("duckdb")
    assert other == "FALSE"  # not a member: nothing matches
    unknown = resolve_context(parse("tenant = current_tenant()"), who("sam")).sql("duckdb")
    assert unknown == "tenant = NULL"  # no tenant in the token: never true, so no rows


def test_masks_keep_the_column_type_except_hashes_which_are_text():
    policy = {
        "column_masks": [
            {"column": "id", "mask": "null"},
            {"column": "email", "mask": "hash"},
            {"column": "salary", "mask": "expression", "expression": "round(salary, -3)"},
        ]
    }
    validate_policy(policy, COLUMNS, "salt")
    sql = wrap(policy, {"columns": COLUMNS}, ("main", "s", "t"), who(), "x", [], "salt").sql(
        "duckdb"
    )
    assert 'CAST(NULL AS BIGINT) AS "id"' in sql
    assert "SHA256('salt' ||" in sql and 'CAST((ROUND(salary, -3)) AS DOUBLE) AS "salary"' in sql
    assert '"region"' in sql  # unmasked columns pass through


def test_hash_masks_need_a_salt_and_unknown_things_are_refused():
    with pytest.raises(LoamzError) as missing:
        validate_policy({"column_masks": [{"column": "email", "mask": "hash"}]}, COLUMNS, "")
    assert http_status(missing.value) == 409
    for bad in (
        {"column_masks": [{"column": "ghost", "mask": "null"}]},
        {"column_masks": [{"column": "id", "mask": "null"}, {"column": "ID", "mask": "redact"}]},
        {"column_masks": [{"column": "id", "mask": "expression"}]},
        {"row_filter": "ghost = 1"},
    ):
        with pytest.raises(LoamzError):
            validate_policy(bad, COLUMNS, "salt")


def test_outer_filters_are_repeated_inside_only_for_unmasked_columns():
    policy = {"column_masks": [{"column": "email", "mask": "null"}], "row_filter": "salary > 0"}
    sql = wrap(
        policy,
        {"columns": COLUMNS},
        ("main", "s", "t"),
        who(),
        "x",
        [("region", "eq", "EU"), ("email", "eq", "a@b.c"), ("id", "in", [1, 2])],
        "salt",
    ).sql("duckdb")
    assert "\"region\" = 'EU'" in sql and '"id" IN (1, 2)' in sql
    assert "a@b.c" not in sql  # a filter on a masked column is never pushed to the raw data


def test_exemptions_match_by_identity_and_empty_policies_apply_to_no_one():
    policy = {"row_filter": "true", "exempt": ["group:auditors", "user:root"]}
    assert applies(policy, who("bob", "analysts"))
    assert not applies(policy, who("bob", "auditors"))
    assert not applies(policy, who("root"))
    assert not applies(None, who()) and not applies({"exempt": ["user:x"]}, who())
    assert isinstance(parse("a = 1"), exp.EQ)
