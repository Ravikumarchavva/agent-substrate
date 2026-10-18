"""Regenerate docs/openapi.json from the application. Run after an intended API change."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from contract import build_schema  # noqa: E402

path = Path(__file__).resolve().parent.parent / "docs" / "openapi.json"
path.write_text(json.dumps(build_schema(), indent=2, sort_keys=True) + "\n")
print(f"Wrote {path}")
