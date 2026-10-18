"""The public API is a contract: any change to it must be a deliberate, reviewed edit of docs/openapi.json."""

from __future__ import annotations

import json

from contract import SCHEMA_FILE, build_schema


def test_openapi_matches_the_checked_in_contract():
    current = json.loads(json.dumps(build_schema(), sort_keys=True))
    expected = json.loads(SCHEMA_FILE.read_text())
    assert current == expected, (
        "API changed: run `uv run python -m scripts.export_openapi` and review the diff"
    )


def test_every_route_is_versioned_and_secured():
    schema = build_schema()
    for path in schema["paths"]:
        assert path.startswith("/api/v1/"), f"unversioned route {path}"
    assert schema["security"] == [{"oidc": []}]
    assert schema["info"]["version"].startswith("1.")
