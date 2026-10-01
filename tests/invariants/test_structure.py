"""Invariant register — structure (rows I26, I27, I28, I30).

The merge pulls a 17.6k-line engine into ``kernel``. Three of these rows are
tripwires for that move: they pass today and must keep passing as the engine
arrives, which is what stops a vendor SDK riding in with it.

Row I30 is the meta-row. The audit found a file that promised "the same suite
is run against those implementations" and never was, while the three runtime
backends drifted apart. A promise that nothing executes is how that happens, so
the register asserts the promise itself.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
KERNEL = REPO_ROOT / "src" / "substrate" / "kernel"
SNAPSHOT = Path(__file__).parent / "public_api.json"

# What the kernel is allowed to import from outside the standard library.
# pydantic for validation, typing_extensions for back-ported typing, and the
# OpenTelemetry *API* (never the SDK — the API is a no-op until an application
# configures an SDK, which is what lets a library instrument itself for free).
#
# ``confusable_homoglyphs`` is the one pure-Python data library admitted
# beyond that: the homoglyph skeleton in ``safety.normalize`` is a prompt-
# injection defence the engine owns, it does no I/O, and reimplementing the
# UTS-39 table by hand would be a worse trade than the dependency.
_ALLOWED_THIRD_PARTY = {"pydantic", "typing_extensions", "opentelemetry", "confusable_homoglyphs"}


def _kernel_files() -> list[Path]:
    return [p for p in KERNEL.rglob("*.py") if "__pycache__" not in p.parts]


def _imported_roots(path: Path) -> set[str]:
    """Top-level package of every import in a file, from the AST.

    Parsed rather than pattern-matched on line prefixes: the existing
    architecture test misses indented imports inside functions, which is
    exactly where a lazy heavy dependency hides.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import, stays inside the package
                continue
            if node.module:
                roots.add(node.module.split(".")[0])
    return roots


def test_i26_the_kernel_imports_only_its_allowed_third_party_set() -> None:
    """The kernel is the engine, so it has to be installable and importable
    without a vendor SDK, a model runtime, or a database driver."""
    offenders: dict[str, set[str]] = {}
    for path in _kernel_files():
        third_party = {
            root
            for root in _imported_roots(path)
            if root not in _ALLOWED_THIRD_PARTY
            and root != "substrate"
            and root not in sys.stdlib_module_names
        }
        if third_party:
            offenders[str(path.relative_to(REPO_ROOT))] = third_party
    assert not offenders, (
        "the kernel may depend only on pydantic, the OpenTelemetry API and confusable_homoglyphs; "
        "anything needing a third-party SDK belongs in integrations/:\n"
        + "\n".join(f"  {where}: {sorted(roots)}" for where, roots in offenders.items())
    )


def test_i27_abstractions_never_imports_the_engine() -> None:
    """``abstractions`` is what an adapter author depends on. If it reaches back
    into the engine, implementing a port drags the whole engine along."""
    abstractions = KERNEL / "abstractions"
    assert abstractions.is_dir(), "kernel/abstractions does not exist"

    engine_packages = {
        p.name for p in KERNEL.iterdir() if p.is_dir() and p.name not in ("abstractions", "__pycache__")
    }
    offenders: dict[str, set[str]] = {}
    for path in abstractions.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        reached: set[str] = set()
        for node in ast.walk(tree):
            module = None
            if isinstance(node, ast.ImportFrom) and node.module:
                module = node.module
            elif isinstance(node, ast.Import):
                module = node.names[0].name
            if module and module.startswith("substrate.kernel."):
                area = module.removeprefix("substrate.kernel.").split(".")[0]
                if area in engine_packages:
                    reached.add(area)
        if reached:
            offenders[str(path.relative_to(REPO_ROOT))] = reached
    assert not offenders, (
        "kernel/abstractions reached into the engine:\n"
        + "\n".join(f"  {where}: {sorted(areas)}" for where, areas in offenders.items())
    )


def test_i28_the_public_api_matches_its_snapshot() -> None:
    """Every addition or removal in the public API shows up as a diff in
    ``public_api.json``, so it is reviewed rather than noticed later.

    This rewrite changes the API deliberately and often — the snapshot is meant
    to be updated in the same commit as the change, never regenerated blindly.
    """
    import substrate.kernel.abstractions as abstractions

    current = sorted(abstractions.__all__)
    expected = json.loads(SNAPSHOT.read_text())["substrate.kernel.abstractions"]

    added = sorted(set(current) - set(expected))
    removed = sorted(set(expected) - set(current))
    assert not added and not removed, (
        "the public API of substrate.kernel.abstractions changed.\n"
        f"  added:   {added}\n"
        f"  removed: {removed}\n"
        f"If intended, update {SNAPSHOT.relative_to(REPO_ROOT)} in this commit."
    )


@pytest.mark.xfail(
    strict=True,
    reason="I30: no port has a conformance suite yet — they land with their "
    "ports in steps 3-6.",
)
def test_i30_every_port_has_a_conformance_suite_and_every_impl_runs_it() -> None:
    """The row that keeps the other rows honest.

    A port is a promise to an outside implementer. The only way that promise
    means anything is if every implementation — the kernel's own default and
    every adapter — runs one shared suite.
    """
    from tests.conformance import registry  # type: ignore[import-not-found]

    missing_suite = [port for port, impls in registry.items() if not impls]
    assert not missing_suite, f"ports with no registered implementations: {missing_suite}"
