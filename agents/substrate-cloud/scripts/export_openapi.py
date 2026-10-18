"""Regenerate the Agent API contract (contracts/agents/v1/openapi.json). Run after an intended API change.

uv run --package substrate-cloud python agents/substrate-cloud/scripts/export_openapi.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from contract import SCHEMA_FILE, schema_text  # noqa: E402

SCHEMA_FILE.parent.mkdir(parents=True, exist_ok=True)
SCHEMA_FILE.write_text(schema_text())
print(f"Wrote {SCHEMA_FILE}")
