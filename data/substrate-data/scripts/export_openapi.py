"""Regenerate docs/openapi.json from the application. Run after an intended API change.

    uv run python scripts/export_openapi.py
    uv run python scripts/export_openapi.py --also ../../contracts/data/v1/openapi.json

`--also PATH` writes the same file to PATH as well (the monorepo's contracts mirror).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from contract import build_schema  # noqa: E402

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument("--also", action="append", default=[], help="write a second copy to this path")
arguments = parser.parse_args()

text = json.dumps(build_schema(), indent=2, sort_keys=True) + "\n"
root = Path(__file__).resolve().parent.parent
for target in [root / "docs" / "openapi.json", *(Path(p).resolve() for p in arguments.also)]:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    print(f"Wrote {target}")
