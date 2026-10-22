"""The public API is a contract: a change to it is a deliberate, reviewed edit of specs/data/v1/openapi.json."""

from __future__ import annotations

from tests.data.contract import SCHEMA_FILE, build_schema, render


def test_openapi_matches_the_checked_in_contract():
    assert render() == SCHEMA_FILE.read_text(), (
        "The data API changed: run `uv run --package loamz-server python server/server/tests/data/contract.py` "
        "and review the diff"
    )


def test_every_route_is_versioned_and_secured():
    schema = build_schema()
    for path in schema["paths"]:
        assert path.startswith("/api/v1/"), f"unversioned route {path}"
    assert schema["security"] == [{"oidc": []}]
    assert schema["info"]["version"].startswith("1.")
