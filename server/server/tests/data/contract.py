"""The OpenAPI document of the data API, built from the routes alone (nothing is started).

Regenerate the committed contract after a deliberate API change, then review the diff:

    uv run --package loamz-data-server python data/server/tests/contract.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from loamz.server.data.app import create_app
from loamz.server.data.kernel.container import Container
from loamz.server.data.kernel.settings import Settings

SCHEMA_FILE = Path(__file__).resolve().parents[4] / "specs" / "data" / "v1" / "openapi.json"


def build_schema() -> dict[str, Any]:
    settings = Settings(_env_file=None, controller_token=SecretStr("x" * 48))
    return create_app(Container(settings), roles={"all"}).openapi()


def render() -> str:
    return json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"


if __name__ == "__main__":
    SCHEMA_FILE.write_text(render())
    print(f"wrote {SCHEMA_FILE}")
