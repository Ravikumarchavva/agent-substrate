"""Builds the Agent API's OpenAPI document from the routes alone (nothing is started)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SCHEMA_FILE = (
    Path(__file__).resolve().parents[3] / "contracts" / "agents" / "v1" / "openapi.json"
)


def build_schema() -> dict[str, Any]:
    from substrate_cloud.monolith.app import create_app

    return create_app().openapi()


def schema_text() -> str:
    return json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"
