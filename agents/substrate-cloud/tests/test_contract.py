"""The public Agent API is a contract: any change to it is a deliberate, reviewed edit of contracts/agents/v1/openapi.json."""

from __future__ import annotations

import json

from contract import SCHEMA_FILE, build_schema

# Unversioned on purpose: infrastructure probes and the well-known key document.
UNVERSIONED = {"/health", "/health/ready"}


def test_openapi_matches_the_checked_in_contract():
    current = json.loads(json.dumps(build_schema(), sort_keys=True))
    expected = json.loads(SCHEMA_FILE.read_text())
    assert current == expected, (
        "API changed: run `uv run --package substrate-cloud python agents/substrate-cloud/scripts/export_openapi.py` "
        "and review the diff of contracts/agents/v1/openapi.json"
    )


def test_every_route_is_versioned_and_secured():
    schema = build_schema()
    for path in schema["paths"]:
        assert path.startswith("/api/v1/") or path in UNVERSIONED, (
            f"unversioned route {path}"
        )
    assert schema["security"] == [{"oidc": []}]
    assert schema["info"]["version"].startswith("1.")
