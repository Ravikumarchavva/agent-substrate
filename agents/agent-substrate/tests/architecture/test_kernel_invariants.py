"""Architecture invariants that keep the kernel frozen.

These checks supplement the ``import-linter`` contract in ``pyproject.toml``
with cheap heuristics that catch regressions early:

* No upward imports (`integrations`, `extensions`, `catalog`, …).
* LOC and file-count ceilings — large new feature additions should not land
  in the kernel.
* No concrete agent / guardrail / middleware classes — only base ABCs.

The ceilings are deliberately loose (20% headroom over current size) so
small kernel refinements don't trip CI, but any *substantial* feature
addition fails the build and forces the "should this be in extensions?"
conversation.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
KERNEL_DIR = REPO_ROOT / "src" / "ravi" / "kernel"

# Loose ceilings — current values are ~15.3k LOC and 102 files. Headroom of
# ~30% covers the remaining S7/S9/S10/S11/S16 kernel contracts while still
# flagging unintended feature drift. The kernel grew from ~11.5k to ~15.3k
# during S8/S12/S13/S14/S15 contract additions (all legitimate — only
# Protocols, ABCs, and pure dataclasses; zero concrete logic).
MAX_KERNEL_LOC = 20_000
MAX_KERNEL_FILES = 130


def _iter_kernel_files() -> list[Path]:
    return [p for p in KERNEL_DIR.rglob("*.py") if "__pycache__" not in p.parts]


def test_kernel_loc_ceiling() -> None:
    files = _iter_kernel_files()
    total = sum(len(p.read_text(encoding="utf-8").splitlines()) for p in files)
    assert total < MAX_KERNEL_LOC, (
        f"Kernel size grew to {total} LOC (ceiling {MAX_KERNEL_LOC}). "
        f"New features should go in ravi/extensions/, not ravi/kernel/."
    )


def test_kernel_file_count_ceiling() -> None:
    n = len(_iter_kernel_files())
    assert n < MAX_KERNEL_FILES, (
        f"Kernel grew to {n} files (ceiling {MAX_KERNEL_FILES}). "
        f"Have you added a new feature module here that belongs in extensions/?"
    )


# Upward-import patterns that must never appear in kernel source.
_FORBIDDEN_PREFIXES = (
    "ravi.extensions",
    "ravi.integrations",
    "ravi.catalog",
    "ravi.server",
    "ravi.services",
    "ravi.shared",
    "ravi.configs",
    "ravi.logger",
)


def test_kernel_has_no_upward_imports() -> None:
    """No file in kernel may import from any layer above it."""
    violations: list[str] = []
    for path in _iter_kernel_files():
        text = path.read_text(encoding="utf-8")
        # Strip docstrings (triple-quoted blocks) before matching — docstring
        # examples that grep would flag are not real imports.
        stripped = re.sub(r'""".*?"""', "", text, flags=re.DOTALL)
        stripped = re.sub(r"'''.*?'''", "", stripped, flags=re.DOTALL)
        for prefix in _FORBIDDEN_PREFIXES:
            for match in re.finditer(
                rf"^\s*(?:from\s+{re.escape(prefix)}|import\s+{re.escape(prefix)})",
                stripped,
                re.MULTILINE,
            ):
                # Report file:position
                relpath = path.relative_to(REPO_ROOT)
                violations.append(f"{relpath}: imports {prefix} ({match.group(0).strip()})")
    assert not violations, (
        "Kernel must not import from layers above it. Violations:\n  "
        + "\n  ".join(violations)
    )


def test_kernel_agents_contains_only_base() -> None:
    """``kernel/agents/`` may define only the base ABC and result dataclasses."""
    agents_dir = KERNEL_DIR / "agents"
    allowed_classes = {
        "BaseAgent",  # ABC
        "PromptEnricher",  # Protocol
        "AgentConfig",  # dataclass / model
        "AgentRunResult",
        "AggregatedUsage",
        "StepResult",
        "ToolCallRecord",
        "RunStatus",  # enum
    }
    found: list[str] = []
    for path in agents_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^class\s+(\w+)\s*[\(:]", text, re.MULTILINE):
            name = match.group(1)
            if name not in allowed_classes:
                relpath = path.relative_to(REPO_ROOT)
                found.append(f"{relpath}: defines {name}")
    assert not found, (
        "Concrete agents must live in ravi/extensions/agents/, not kernel/. "
        "Found in kernel:\n  " + "\n  ".join(found)
    )


def test_kernel_guardrails_contains_only_base() -> None:
    """``kernel/guardrails/`` may define only the ABC and result types."""
    guardrails_dir = KERNEL_DIR / "guardrails"
    allowed_classes = {
        "BaseGuardrail",  # ABC
        "GuardrailContext",  # dataclass
        "GuardrailResult",  # dataclass
        "GuardrailType",  # enum
    }
    found: list[str] = []
    for path in guardrails_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^class\s+(\w+)\s*[\(:]", text, re.MULTILINE):
            name = match.group(1)
            if name not in allowed_classes:
                relpath = path.relative_to(REPO_ROOT)
                found.append(f"{relpath}: defines {name}")
    assert not found, (
        "Concrete guardrails must live in ravi/extensions/guardrails/. "
        "Found in kernel:\n  " + "\n  ".join(found)
    )


def test_kernel_middleware_contains_only_base() -> None:
    """``kernel/middleware/`` may define only the ABC and the pipeline runner."""
    middleware_dir = KERNEL_DIR / "middleware"
    allowed_classes = {
        "BaseMiddleware",  # ABC
        "MiddlewareContext",  # dataclass
        "MiddlewareStage",  # enum
        "MiddlewarePipeline",  # generic orchestrator (pure infrastructure)
    }
    found: list[str] = []
    for path in middleware_dir.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^class\s+(\w+)\s*[\(:]", text, re.MULTILINE):
            name = match.group(1)
            if name not in allowed_classes:
                relpath = path.relative_to(REPO_ROOT)
                found.append(f"{relpath}: defines {name}")
    assert not found, (
        "Concrete middleware must live in ravi/extensions/middleware/. "
        "Found in kernel:\n  " + "\n  ".join(found)
    )
