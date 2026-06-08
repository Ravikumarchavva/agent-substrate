"""Architecture invariants that keep the kernel frozen.

These checks supplement the ``import-linter`` contracts in ``pyproject.toml``
with cheap heuristics that catch regressions early:

* No upward imports — the kernel (L0) must not import any layer above it
  (agents, capabilities, fabric) nor orthogonal modules (integrations, serving).
* LOC and file-count ceilings — large new feature additions must not land
  in the kernel.
* Flat layout — kernel contains no subdirectories (every file is top-level
  in kernel/). Subdirectories would suggest a concrete feature sub-package
  rather than a collection of contracts.

The kernel holds ALL contracts: Protocols, dataclasses, enums, value types.
Concrete implementations live in agents (L1), capabilities (L2), fabric (L3),
or integrations (orthogonal). The ceilings carry ~20% headroom over current
size so small contract refinements don't trip CI, while substantial concrete
additions fail the build and force the "which layer?" conversation.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KERNEL_DIR = REPO_ROOT / "src" / "ravi" / "kernel"

# Ceilings — current values are ~2k LOC and 17 files.
# ~20% headroom keeps small contract additions from nagging CI.
MAX_KERNEL_LOC = 14_000
MAX_KERNEL_FILES = 115


def _iter_kernel_files() -> list[Path]:
    return [p for p in KERNEL_DIR.rglob("*.py") if "__pycache__" not in p.parts]


def test_kernel_loc_ceiling() -> None:
    files = _iter_kernel_files()
    total = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in files)
    assert total < MAX_KERNEL_LOC, (
        f"Kernel grew to {total} LOC (ceiling {MAX_KERNEL_LOC}). "
        "Concrete code belongs in agents/capabilities/fabric/integrations, "
        "not ravi/kernel/ — the kernel holds contracts only."
    )


def test_kernel_file_count_ceiling() -> None:
    n = len(_iter_kernel_files())
    assert n < MAX_KERNEL_FILES, (
        f"Kernel grew to {n} files (ceiling {MAX_KERNEL_FILES}). "
        "Have you added a feature module that belongs in a layer above?"
    )


def test_kernel_is_flat() -> None:
    """Kernel must not contain subdirectories (other than __pycache__).

    A subdirectory in kernel/ would imply a concrete feature sub-package.
    All contracts live as flat .py files directly under kernel/.
    """
    subdirs = [
        p for p in KERNEL_DIR.iterdir()
        if p.is_dir() and p.name != "__pycache__"
    ]
    assert not subdirs, (
        "Kernel must be a flat collection of contract files — no subdirectories. "
        "Move these to the appropriate layer above kernel/:\n  "
        + "\n  ".join(str(d.relative_to(REPO_ROOT)) for d in subdirs)
    )


# Upward-import patterns that must never appear in kernel source.
# Includes all layers above L0 and all orthogonal modules.
_FORBIDDEN_PREFIXES = (
    # Stack layers above kernel
    "ravi.agents",
    "ravi.capabilities",
    "ravi.fabric",
    # Orthogonal modules
    "ravi.integrations",
    "ravi.serving",
    # Top-level helpers (kernel must be self-contained)
    "ravi.config",
    "ravi.logger",
)


def test_kernel_has_no_upward_imports() -> None:
    """No file in kernel/ may import from any layer above it."""
    violations: list[str] = []
    for path in _iter_kernel_files():
        text = path.read_text(encoding="utf-8")
        # Strip triple-quoted docstrings before scanning — usage examples that
        # contain import snippets must not be flagged as real violations.
        stripped = re.sub(r'""".*?"""', "", text, flags=re.DOTALL)
        stripped = re.sub(r"'''.*?'''", "", stripped, flags=re.DOTALL)
        for prefix in _FORBIDDEN_PREFIXES:
            for match in re.finditer(
                rf"^\s*(?:from\s+{re.escape(prefix)}|import\s+{re.escape(prefix)})",
                stripped,
                re.MULTILINE,
            ):
                relpath = path.relative_to(REPO_ROOT)
                violations.append(
                    f"{relpath}: imports {prefix!r} ({match.group(0).strip()})"
                )
    assert not violations, (
        "Kernel must not import from any layer above it. Violations:\n  "
        + "\n  ".join(violations)
    )
