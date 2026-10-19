"""Fail when the retired shared-secret JWT scheme reappears anywhere in the repository (ADR 0007).

    python scripts/check_no_shared_secrets.py            # exit 1 and list the hits

`JWT_SECRET`, `ENGINE_JWT_SECRET` and `HS256` must not appear in code, configuration, deployment files or tests, except where a
file exists to prove that such tokens are refused (ALLOWED below, each with its reason). Documentation (docs/, Markdown) and
the decision records are excluded: they may explain what was removed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PATTERN = re.compile(r"ENGINE_JWT_SECRET|JWT_SECRET|HS256")
SKIP_SUFFIXES = {
    ".md",
    ".lock",
    ".png",
    ".jpg",
    ".ico",
    ".pdf",
    ".svg",
    ".woff",
    ".woff2",
    ".sqlite",
    ".db",
}
SKIP_NAMES = {"pnpm-lock.yaml", "uv.lock"}
SKIP_DIRS = {"docs"}
# Files that exist to prove the old tokens are refused: the only places the names may appear.
ALLOWED = {
    "scripts/check_no_shared_secrets.py": "this guard",
    "identity/substrate-identity/tests/test_verifier.py": "asserts the verifier refuses HS256",
    "data/substrate-data/tests/test_auth.py": "asserts substrate-data refuses HS256",
    "agents/substrate-cloud/tests/_oidc.py": "mints an HS256 token to prove services refuse it",
    "agents/substrate-cloud/tests/test_oidc_entry_points.py": "proves every entry point refuses HS256",
    "agents/substrate-cloud/tests/test_no_shared_secrets.py": "tests this guard",
    "agents/substrate-ui/src/lib/session.test.ts": "proves the console refuses an HS256 bearer",
}


# Pending removal, owned by the packaging track: the legacy compose files (OnlyOffice's own JWT setting included).
ALLOWED_PREFIXES = {
    "deploy/legacy-agents/": "legacy deployment files, replaced by the cell packaging (remove with them)"
}


def tracked_files() -> list[Path]:
    listing = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    return [ROOT / name for name in listing.split("\0") if name]


def find_violations() -> list[str]:
    hits = []
    for path in tracked_files():
        relative = path.relative_to(ROOT).as_posix()
        if (
            not path.is_file()
            or relative.split("/")[0] in SKIP_DIRS
            or path.suffix in SKIP_SUFFIXES
            or path.name in SKIP_NAMES
            or relative in ALLOWED
            or relative.startswith(tuple(ALLOWED_PREFIXES))
        ):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError, OSError:
            continue
        for number, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line):
                hits.append(f"{relative}:{number}: {line.strip()[:120]}")
    return hits


if __name__ == "__main__":
    found = find_violations()
    for hit in found:
        print(hit)
    if found:
        print(
            f"\n{len(found)} use(s) of the retired shared-secret scheme (ADR 0007). Use substrate-identity.",
            file=sys.stderr,
        )
        sys.exit(1)
    print("no shared-secret JWT scheme found")
