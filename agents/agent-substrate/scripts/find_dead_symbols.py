#!/usr/bin/env python3
"""Flag module-level functions/classes in src/substrate/ with no real usage.

Method: collect every top-level (module-scope) ``def``/``class`` name across
src/substrate/, excluding underscore-prefixed and dunder names. Then walk
every .py file under src/substrate/ and tests/, counting each identifier's
appearances as an ast.Name in Load context (called, instantiated, used as a
type, passed around), plus two non-Name reference forms that are common here
and would otherwise read as false positives: `import X as _X` aliases (the
LLM client modules), and string forward-refs like SQLAlchemy's
`Mapped["User"]` / `relationship("User")` — this also means any name quoted
verbatim anywhere (a docstring mention, an `__all__` entry) counts as a
reference, which trades a few false negatives for far fewer false positives.

A symbol with zero Name-Load occurrences anywhere is CONFIRMED unused: the
identifier string never appears as a real reference in the entire codebase.
A symbol whose only occurrences are inside its own defining file is SUSPECT:
plausibly module-private (should probably be underscore-prefixed) or
genuinely dead outside a self-referential edge (e.g. recursion) — needs a
human look, not an assertion.

This is deliberately module-level only: methods and nested functions need
call-site resolution this AST-only pass doesn't attempt, since that's where
naive graph/degree tools (e.g. a knowledge-graph tool evaluated 2026-07-12)
were shown to false-positive on real, live code (Supervision.spawn_child).

Known limitation: name collisions. Two unrelated top-level symbols sharing a
name (e.g. `Foo` defined in two different modules) will under-count each
other's dead-ness — a real reference to one masks the other looking unused.
Treat CONFIRMED as "investigate," not "delete on sight."

Usage: uv run python scripts/find_dead_symbols.py [--json]
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "substrate"
TESTS_ROOT = REPO_ROOT / "tests"

# Legacy code is explicitly out of scope (root CLAUDE.md: "do not import").
EXCLUDE_DIRS = {"__pycache__", "legacy"}


@dataclass
class Symbol:
    name: str
    kind: str  # "function" | "class"
    file: Path
    lineno: int


@dataclass
class UsageIndex:
    counts: dict[str, int] = field(default_factory=dict)
    own_file_only: dict[str, set[Path]] = field(default_factory=dict)

    def record(self, name: str, file: Path) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1
        self.own_file_only.setdefault(name, set()).add(file)


def _iter_py_files(root: Path) -> list[Path]:
    return [
        p
        for p in root.rglob("*.py")
        if not any(part in EXCLUDE_DIRS for part in p.parts)
    ]


def _collect_top_level_symbols(files: list[Path]) -> list[Symbol]:
    symbols: list[Symbol] = []
    for file in files:
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "function"
            elif isinstance(node, ast.ClassDef):
                kind = "class"
            else:
                continue
            name = node.name
            if name.startswith("_"):
                continue
            # Decorated top-level defs are typically dispatched by a
            # framework/registry (FastAPI route decorators, tool
            # auto-discovery via CatalogScanner) rather than called by name
            # anywhere in Python source — not a reliable dead-code signal.
            if node.decorator_list:
                continue
            symbols.append(Symbol(name=name, kind=kind, file=file, lineno=node.lineno))
    return symbols


def _collect_usages(files: list[Path]) -> UsageIndex:
    index = UsageIndex()
    for file in files:
        try:
            tree = ast.parse(file.read_text(encoding="utf-8"), filename=str(file))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                index.record(node.id, file)
            # `from x import Y as Z` — a reference to alias Z is really a
            # reference to Y; without this, every re-exported/aliased
            # symbol (common in this codebase's LLM client modules) looks
            # unused because only the alias ever appears as an ast.Name.
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.asname and alias.asname != alias.name:
                        index.record(alias.name, file)
            # String forward-refs, e.g. SQLAlchemy `Mapped["User"]` /
            # `relationship("User")` or a quoted type annotation — these
            # are real references but never produce an ast.Name node.
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                index.record(node.value, file)
    return index


def find_dead_symbols() -> tuple[list[Symbol], list[Symbol]]:
    src_files = _iter_py_files(SRC_ROOT)
    all_files = src_files + _iter_py_files(TESTS_ROOT)

    symbols = _collect_top_level_symbols(src_files)
    usage = _collect_usages(all_files)

    confirmed: list[Symbol] = []
    suspect: list[Symbol] = []
    for sym in symbols:
        count = usage.counts.get(sym.name, 0)
        if count == 0:
            confirmed.append(sym)
        elif usage.own_file_only.get(sym.name) == {sym.file}:
            suspect.append(sym)
    return confirmed, suspect


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json", action="store_true", help="emit machine-readable JSON"
    )
    args = parser.parse_args()

    confirmed, suspect = find_dead_symbols()

    def _to_dicts(items: list[Symbol]) -> list[dict[str, object]]:
        return [
            {
                "name": s.name,
                "kind": s.kind,
                "file": str(s.file.relative_to(REPO_ROOT)),
                "line": s.lineno,
            }
            for s in items
        ]

    if args.json:
        payload = {
            "confirmed": _to_dicts(confirmed),
            "suspect": _to_dicts(suspect),
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(f"# Dead symbol scan — {len(confirmed)} confirmed, {len(suspect)} suspect\n")

    print(
        "## CONFIRMED — identifier never referenced anywhere in src/substrate or tests"
    )
    for s in sorted(confirmed, key=lambda s: str(s.file)):
        rel = s.file.relative_to(REPO_ROOT)
        print(f"  {rel}:{s.lineno}  {s.kind} {s.name}")

    print(
        "\n## SUSPECT — only referenced inside its own defining file (module-private?)"
    )
    for s in sorted(suspect, key=lambda s: str(s.file)):
        rel = s.file.relative_to(REPO_ROOT)
        print(f"  {rel}:{s.lineno}  {s.kind} {s.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
